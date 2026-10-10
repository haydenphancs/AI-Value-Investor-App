"""
Company Weekly (Drop 2a) — the TEMPLATE day in the script flow, and the ledger's template gates
(contract D10-D13, owner decisions 2026-10-09).

What is pinned here:

* `script_service` (D11): the series calendar and the class switch decide the day; a news chain becomes a
  BACKGROUND build (the kick waits at most TEMPLATE_KICK_WAIT_SECONDS, a second kick joins the same task,
  `shutdown` cancels it); the candidate loop composes each record from its JSON round trip, re-checks it
  (`news_templates.revalidate`) as stored, attaches its logos and freezes its formats keeping the
  template's own footer; ONE first-write-wins INSERT (an `accepted` row: template authorship, 0 tokens, no
  model); every fallback lands in the trail (`fact_sheet.selection`) and on the run mirror
  (`series`, `series_trail`), and the chain always ends in the lesson; the default classes ("A") are
  byte-identical to Drop 1, and the lesson rotation never sees news refs.
* `run_service` (D10, D12): `store_logo` (content-addressed, never overwritten or deleted), the template
  branch of `create_posts` (class from the SCRIPT, the switch, the re-check, never auto-approved, the post
  metadata and its `made_with_ai`), and the template branches of the on-screen checks.
* the timing constants against the worker's own (an AST read of marketing/main.py).

Hermetic: the in-memory PostgREST fake of test_marketing_script_flow.py plus a Storage fake, a fake
company-news source (the adapter's public surface), real records / templates / on-screen checks.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import json
import logging
import struct
import uuid
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.schemas.marketing import VIDEO_BRAND_TEXT, WorkerScript
from app.services.marketing import company_news_rules as R
from app.services.marketing import content_pool, logo_check, news_templates, post_copy
from app.services.marketing import run_service as mrs
from app.services.marketing import script_service as ss
from app.services.marketing import selection
from app.services.marketing import template_onscreen as onscreen
from test_marketing_script_flow import NONCE, FakeSB, FakeWriter

_BACKEND = Path(__file__).resolve().parents[1]

MON, TUE, THU, SAT = date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 19), date(2026, 11, 21)
assert (MON.weekday(), TUE.weekday(), THU.weekday(), SAT.weekday()) == (0, 1, 3, 5)
_SB_PUBLIC = "https://sb.example"


# ── records (role-only rows; fictional people only) ───────────────────────────


def co(sym: str, name: str) -> R.CompanyRef:
    return R.CompanyRef(symbol=sym, name=name)


def buy(sym, name, role, amount, shares, filings, *, person=None, holding="direct") -> R.InsiderPurchase:
    return R.InsiderPurchase(company=co(sym, name), role=role, person_name=person, amount_usd=amount,
                             shares=shares, purchases=len(filings), earliest_trade_date=filings[0],
                             latest_trade_date=filings[-1], filing_dates=tuple(filings), holding=holding,
                             amended=False)


def ceo_week(run_date: date = MON, series: str = "ceo_buys") -> R.InsiderBuysWeek:
    start, end = R.insider_window(run_date)
    role = "ceo" if series == "ceo_buys" else "director"
    return R.InsiderBuysWeek(series=series, window_start=start, window_end=end, rows=(
        buy("LOW", "Lowe's", role, 2_300_000.0, 9_812.0, [end - timedelta(days=3)], holding="indirect"),
        buy("SBUX", "Starbucks", role, 410_000.0, 4_850.0, [end - timedelta(days=2)]),
    ))


def costco(**kw) -> R.MoneyMap:
    base = dict(series="money_map", company=co("COST", "Costco"), fiscal_year="2025", period_end=date(2025, 8, 31),
                segments=(R.Segment("United States", 200_000_000_000.0), R.Segment("Canada", 38_500_000_000.0),
                          R.Segment("Other International", 36_700_000_000.0)),
                other_usd=None, eliminations_usd=None, revenue_usd=275_200_000_000.0,
                gross_profit_usd=35_400_000_000.0, operating_profit_usd=10_400_000_000.0,
                net_income_usd=8_100_000_000.0)
    base.update(kw)
    return R.MoneyMap(**base)


def stale_costco() -> R.MoneyMap:
    """Valid as a record, refused by its template (the fiscal year ended too long before the run)."""
    return costco(fiscal_year="2022", period_end=date(2022, 8, 28))


def cands(series: str, *records, skip: Optional[str] = None, rejections: Optional[Dict[str, int]] = None):
    """The adapter's `Candidates` surface (series, records, skip_reason, rejections)."""
    return SimpleNamespace(series=series, records=tuple(records),
                           skip_reason=skip if not records else None, rejections=dict(rejections or {}))


# ── a PNG the logo header check accepts ───────────────────────────────────────


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png(width: int = 120, height: int = 120, shade: int = 7) -> bytes:
    raw = b"".join(b"\x00" + bytes([shade, 40, 90]) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))


# ── fakes: the company-news source and Storage ────────────────────────────────


class NewsUnavailable(Exception):
    """The adapter's `MarketingNewsUnavailable(series, reason, detail)` surface."""

    def __init__(self, series: str, reason: str = "internal_error", detail: str = "") -> None:
        super().__init__(f"{series}: {reason} {detail}".strip())
        self.series, self.reason, self.detail = series, reason, detail


class FakeNews:
    MarketingNewsUnavailable = NewsUnavailable

    def __init__(self, answers: Dict[str, Any], *, logos: Optional[Dict[str, Any]] = None) -> None:
        self.answers = answers
        self.logos = logos if logos is not None else {}
        self.calls: List[Dict[str, Any]] = []
        self.logo_calls: List[str] = []
        self.gate: Optional[asyncio.Event] = None

    async def candidates(self, series, *, run_date, exclude, limit, deadline):
        self.calls.append({"series": series, "run_date": run_date, "exclude": exclude, "limit": limit,
                           "deadline": deadline})
        if self.gate is not None:
            await self.gate.wait()
        answer = self.answers.get(series)
        if callable(answer):
            answer = await answer()
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            return cands(series, skip=f"{series}_none_qualified")
        return answer

    async def fetch_logo(self, symbol, *, max_bytes, timeout):
        self.logo_calls.append(symbol)
        answer = self.logos.get(symbol, (png(shade=len(self.logo_calls) % 200), "image/png"))
        if isinstance(answer, BaseException):
            raise answer
        return answer


class StorageConflict(Exception):
    """storage3's StorageApiError surface for a refused `x-upsert: false` upload."""

    def __init__(self) -> None:
        super().__init__("{'statusCode': 409, 'error': Duplicate, 'message': The resource already exists}")
        self.status, self.code = 409, "Duplicate"


class FakeStorage:
    """One bucket: `objects` path → (bytes, content type). `list` answers what Storage records for an
    object (size, mimetype); `upload` refuses an existing key (409) like `x-upsert: false`."""

    def __init__(self) -> None:
        self.objects: Dict[str, tuple] = {}
        self.uploads: List[tuple] = []
        self.removed: List[str] = []
        self.fail_upload: Optional[BaseException] = None
        self.fail_list: Optional[BaseException] = None
        self.race: Optional[tuple] = None     # (bytes, type) another writer lands just before our upload

    def from_(self, _bucket):
        return self

    def list(self, prefix, options=None):
        if self.fail_list is not None:
            raise self.fail_list
        name = (options or {}).get("search")
        return [{"name": p.rsplit("/", 1)[-1], "metadata": {"size": len(d), "mimetype": t}}
                for p, (d, t) in self.objects.items()
                if p.rsplit("/", 1)[0] == prefix and (not name or p.endswith(name))]

    def upload(self, path, data, options=None):
        self.uploads.append((path, dict(options or {})))
        if self.fail_upload is not None:
            raise self.fail_upload
        if self.race is not None:
            self.objects[path], self.race = self.race, None
        if path in self.objects:
            raise StorageConflict()
        self.objects[path] = (bytes(data), (options or {}).get("content-type"))
        return {"path": path}

    def remove(self, paths):
        self.removed.extend(paths)


# ── the world ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tworld(monkeypatch):
    sb = FakeSB()
    sb.storage = FakeStorage()
    runs = mrs.MarketingRunService(supabase=sb)
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", False)
    monkeypatch.setattr(mrs.settings, "MARKETING_RUN_STALE_SECONDS", 2700)
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_POSTS", True)
    monkeypatch.setattr(mrs.settings, "MARKETING_X_IMAGES", False)
    monkeypatch.setattr(mrs.settings, "MARKETING_X_ALLOW_URLS", False)
    monkeypatch.setattr(mrs.settings, "SUPABASE_URL", _SB_PUBLIC)
    monkeypatch.setattr(ss.smart_link, "store_state", lambda: "live")
    monkeypatch.setattr(ss, "_FINISH_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(ss, "_REFRESH_BACKOFF_SECONDS", 0.0)
    return sb, runs


def _today(monkeypatch, day: date) -> None:
    monkeypatch.setattr(ss, "_today_et", lambda: day)


#: What the drop-2 worker declares on its claim (marketing/main.py BackendClient.WORKER_CAPABILITIES).
DROP2_CAPS = ["layouts_2b", "news_templates", "post_image"]
#: What a drop-2a worker image declared: it draws templates, but refuses a `pair` / `grid` image.
DROP2A_CAPS = ["news_templates", "post_image"]


def _run(sb: FakeSB, day: date, *, caps: Optional[List[str]] = None, **extra) -> str:
    rid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    meta: Dict[str, Any] = {"claim_nonce": NONCE}
    caps = DROP2_CAPS if caps is None else caps
    if caps:
        meta["worker_capabilities"] = list(caps)
    sb.tables[mrs.RUNS].rows.append({"id": rid, "run_date": day.isoformat(), "status": "in_progress",
                                     "stage": "planned", "content_class": "A", "metadata": meta,
                                     "worker_version": "drop2a-test",
                                     "timings": {}, "attempts": 1, "dry_run": True, "started_at": now,
                                     "updated_at": now, **extra})
    return rid


def _claim(sb: FakeSB, rid: str) -> mrs.CallerClaim:
    row = _run_row(sb, rid)
    return mrs.CallerClaim(int(row["attempts"]), row["metadata"]["claim_nonce"])


def _run_row(sb, rid):
    return next(r for r in sb.tables[mrs.RUNS].rows if r["id"] == rid)


def _script(sb, rid):
    return next(r for r in sb.tables[mrs.SCRIPTS].rows if r["run_id"] == rid)


def _seed_recent(sb: FakeSB, day: date, ref: str) -> None:
    sb.tables[mrs.SCRIPTS].rows.append({"run_id": str(uuid.uuid4()), "run_date": day.isoformat(),
                                        "status": "accepted", "source_ref": ref})


async def _drain(svc: ss.MarketingScriptService) -> None:
    for _ in range(50):
        if not svc._tasks:
            return
        await asyncio.sleep(0)
        await asyncio.gather(*list(svc._tasks), return_exceptions=True)


def _svc(runs, news, writer=None) -> ss.MarketingScriptService:
    return ss.MarketingScriptService(runs, writer=writer or FakeWriter(["accepted"]), news=news)


# ══ a Monday with class C on: one ACCEPTED template row, the writer never called ══════════════════


@pytest.mark.asyncio
async def test_a_monday_with_class_c_on_accepts_one_template_and_never_calls_the_writer(tworld, monkeypatch,
                                                                                         caplog):
    sb, runs = tworld
    _today(monkeypatch, MON)
    week = ceo_week()
    news = FakeNews({"ceo_buys": cands("ceo_buys", week, rejections={"price_implausible": 2})})
    writer = FakeWriter(["accepted"])
    svc = _svc(runs, news, writer)
    rid = _run(sb, MON)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "accepted" and state["template_id"] == "ceo_buys"
    assert state["source_ref"] == R.ledger_key(week) == "news:ceo_buys:2026-11-09"
    assert writer.calls == []                                   # no model: a template day costs 0 tokens
    (row,) = sb.tables[mrs.SCRIPTS].rows
    assert (row["status"], row["template_id"], row["tokens_used"], row["generations"], row["model"]) == (
        "accepted", "ceo_buys", 0, 0, None)
    assert row["prompt_version"] == news_templates.TEMPLATE_VERSION and row["violations"] == []
    out = row["output"]
    assert (out["authorship"], out["content_class"], out["series"]) == ("template", "C", "ceo_buys")
    # formats frozen: the video platforms video; the text platforms the image (X stays text: its switch is off)
    assert out["post_formats"]["tiktok"] == "video" and out["post_formats"]["bluesky"] == "image"
    assert out["post_formats"]["x"] == "text"
    # the template's OWN footer, never the lesson's AI footer
    assert out["image_footer"] == out["image_spec"]["footer"]
    assert out["image_footer"].startswith("Educational only · not investment advice · Source: SEC Form 4 filings")
    assert "AI" not in out["image_footer"]
    # one logo entry per ref, in draw order, stored content-addressed
    assert [(lg["key"], lg["name"]) for lg in out["logos"]] == [(r["key"], r["name"]) for r in out["logo_refs"]]
    assert all(lg["url"].startswith(f"{_SB_PUBLIC}/storage/v1/object/public/marketing-media/logos/")
               for lg in out["logos"])
    # the fact sheet: the record, the adapter's rejections, the selection with its trail
    sheet = row["fact_sheet"]
    assert R.record_from_fact_sheet(sheet) == week
    assert sheet["rejections"] == {"price_implausible": 2}
    assert sheet["selection"] == {"plan": "monday", "chain": ["ceo_buys", "insider_buys", "money_map", "lesson"],
                                  "trail": [{"series": "ceo_buys", "outcome": "chosen"}]}
    # stored exactly as create_posts will re-check it
    assert news_templates.revalidate(out, fact_sheet=sheet, run_date=MON) == []
    # the run mirror: the script's class and series
    run = _run_row(sb, rid)
    assert (run["source_ref"], run["template_id"], run["content_class"]) == (state["source_ref"], "ceo_buys", "C")
    assert run["metadata"]["series"] == "ceo_buys" and run["metadata"]["series_trail"] == [
        {"series": "ceo_buys", "outcome": "chosen"}]
    assert run["metadata"]["claim_nonce"] == NONCE                    # the merge kept the claim
    # the worker's script: validated fields, round-tripping the wire schema
    script = WorkerScript.model_validate(state["script"]).model_dump()
    assert (script["content_class"], script["authorship"], script["series"], script["video_layout"]) == (
        "C", "template", "ceo_buys", "per_line")
    assert script["opening_card"] == out["opening_card"] and script["image_spec"] == out["image_spec"]
    assert [lg["key"] for lg in script["logos"]] == [lg["key"] for lg in out["logos"]]
    assert script["image_footer"] == out["image_footer"]
    # the adapter was asked once, with the ledger as its exclude set and the series' budget
    (call,) = news.calls
    assert call["series"] == "ceo_buys" and call["limit"] == ss.MAX_CANDIDATES_PER_SERIES
    assert any("marketing template ACCEPTED" in r.getMessage() and rid in r.getMessage() for r in caplog.records)
    # idempotent: the next kick answers the same row, nothing rebuilt
    again = await svc.kick(rid, claim=_claim(sb, rid))
    assert again["status"] == "accepted" and len(news.calls) == 1 and len(sb.tables[mrs.SCRIPTS].rows) == 1


@pytest.mark.asyncio
async def test_the_ledger_is_the_exclude_set_and_the_rotation_never_sees_it(tworld, monkeypatch):
    """The build reads NEWS_RECENT_LIMIT rows (news refs included) and hands them to the adapter as its
    exclude set — a candidate already posted is the adapter's `already_posted`."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    _seed_recent(sb, MON - timedelta(days=7), "news:ceo_buys:2026-11-02")
    _seed_recent(sb, MON - timedelta(days=2), "money_moves:x")
    news = FakeNews({"ceo_buys": cands("ceo_buys", ceo_week())})
    svc = _svc(runs, news)
    rid = _run(sb, MON)
    await svc.kick(rid, claim=_claim(sb, rid))
    assert news.calls[0]["exclude"] == frozenset({"news:ceo_buys:2026-11-02", "money_moves:x"})


# ══ every fallback outcome lands in the trail and on the run ═════════════════════════════════════


async def _slow():
    await asyncio.sleep(5)


@pytest.mark.parametrize("answer, outcome, reason", [
    (None, "no_candidates", "ceo_buys_none_qualified"),
    (cands("ceo_buys", skip="ceo_none_qualified", rejections={"already_posted": 1}), "all_recent",
     "ceo_none_qualified"),
    (cands("ceo_buys", skip="ceo_none_qualified", rejections={"already_posted": 1, "below_cap_floor": 2}),
     "no_candidates", "ceo_none_qualified"),
    (NewsUnavailable("ceo_buys", "insider_feed_unavailable"), "unavailable", "insider_feed_unavailable"),
    (RuntimeError("bug in the adapter"), "error", "RuntimeError"),
    (ValueError("unknown or unshipped company-news series"), "error", "ValueError"),
    (cands("ceo_buys", costco()), "all_refused", "record_invalid"),          # a money map offered as ceo_buys
])
@pytest.mark.asyncio
async def test_each_fallback_outcome_lands_in_the_trail_and_on_the_run(tworld, monkeypatch, caplog, answer,
                                                                      outcome, reason):
    sb, runs = tworld
    _today(monkeypatch, MON)
    news = FakeNews({"ceo_buys": answer, "insider_buys": None, "money_map": cands("money_map", costco())})
    svc = _svc(runs, news)
    rid = _run(sb, MON)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "accepted" and state["template_id"] == "money_map"
    trail = [{"series": "ceo_buys", "outcome": outcome, "reason": reason},
             {"series": "insider_buys", "outcome": "no_candidates", "reason": "insider_buys_none_qualified"},
             {"series": "money_map", "outcome": "chosen"}]
    assert _script(sb, rid)["fact_sheet"]["selection"]["trail"] == trail
    meta = _run_row(sb, rid)["metadata"]
    assert meta["series"] == "money_map" and meta["series_trail"] == trail
    assert _run_row(sb, rid)["content_class"] == "F"
    fell = [r for r in caplog.records if "marketing series FELL BACK" in r.getMessage()]
    assert len(fell) == 2 and f"outcome={outcome}" in fell[0].getMessage() and "next=insider_buys" in fell[0].getMessage()
    if outcome == "all_refused":
        assert any(r.levelno == logging.ERROR and "refused every candidate" in r.getMessage() for r in caplog.records)
    if outcome == "error":
        assert any(r.levelno == logging.ERROR and r.exc_info for r in caplog.records)


@pytest.mark.asyncio
async def test_a_series_that_outlives_its_budget_is_a_timeout(tworld, monkeypatch):
    sb, runs = tworld
    _today(monkeypatch, MON)
    monkeypatch.setitem(ss.SERIES_BUDGET_SECONDS, "ceo_buys", 0.01)
    news = FakeNews({"ceo_buys": _slow, "money_map": cands("money_map", costco())})
    svc = _svc(runs, news)
    monkeypatch.setattr(ss, "TEMPLATE_KICK_WAIT_SECONDS", 10.0)
    rid = _run(sb, MON)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["template_id"] == "money_map"
    trail = _script(sb, rid)["fact_sheet"]["selection"]["trail"]
    assert trail[0]["series"] == "ceo_buys" and trail[0]["outcome"] == "timeout"


@pytest.mark.asyncio
async def test_a_series_with_no_budget_left_is_never_started(tworld, monkeypatch):
    """Under SERIES_MIN_START_SECONDS of the build budget a series falls through as `budget` without a
    call; the chain still ends in the lesson (never a silent day)."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    clock = iter([0.0] + [ss.TEMPLATE_BUILD_BUDGET_SECONDS] * 50)
    monkeypatch.setattr(ss, "_mono", lambda: next(clock))
    news = FakeNews({})
    svc = _svc(runs, news)
    rid = _run(sb, MON)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert news.calls == [] and state["status"] == "generating" and state["template_id"] in selection.LESSON_TEMPLATE_IDS
    trail = _script(sb, rid)["fact_sheet"]["selection"]["trail"]
    assert [t["outcome"] for t in trail] == ["budget", "budget", "budget", "chosen"]
    assert trail[-1]["series"] == "lesson"
    await _drain(svc)


@pytest.mark.asyncio
async def test_a_day_where_no_series_posts_falls_to_the_weekly_lesson_and_keeps_its_trail(tworld, monkeypatch):
    """Every news series came up empty: the lesson (class A, the writer) with one of LESSON_TEMPLATE_IDS;
    the selection block rides in its fact sheet, survives the accepted-package rewrite, and reaches the
    run and the posts."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    news = FakeNews({})
    writer = FakeWriter(["accepted"])
    svc = _svc(runs, news, writer)
    rid = _run(sb, MON)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "generating" and state["template_id"] in selection.LESSON_TEMPLATE_IDS
    await _drain(svc)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "accepted" and len(writer.calls) == 1
    row = _script(sb, rid)
    block = row["fact_sheet"]["selection"]
    assert [t["series"] for t in block["trail"]] == ["ceo_buys", "insider_buys", "money_map", "lesson"]
    assert row["fact_sheet"]["sentences"]                        # the lesson's own sheet, rewritten on accept
    run = _run_row(sb, rid)
    assert run["content_class"] == "A" and run["metadata"]["series"] == "lesson"
    posts = await runs.create_posts(rid, [{"platform": "x", "format": "text"}], claim=_claim(sb, rid))
    md = posts[0]["metadata"]
    assert (md["content_class"], md["series"], md["authorship"], md["made_with_ai"]) == ("A", "lesson", "ai", True)
    assert [t["series"] for t in md["series_trail"]] == ["ceo_buys", "insider_buys", "money_map", "lesson"]


@pytest.mark.asyncio
async def test_the_first_candidate_refused_lets_the_next_one_post(tworld, monkeypatch):
    sb, runs = tworld
    _today(monkeypatch, THU)
    news = FakeNews({"money_map": cands("money_map", stale_costco(), costco())})
    svc = _svc(runs, news)
    rid = _run(sb, THU)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "accepted" and state["source_ref"] == "news:money_map:COST:2025"
    assert _script(sb, rid)["fact_sheet"]["selection"]["trail"] == [{"series": "money_map", "outcome": "chosen"}]


# ══ the background build: generating, join, race, shutdown ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_a_slow_build_answers_generating_and_a_second_kick_joins_the_same_task(tworld, monkeypatch):
    sb, runs = tworld
    _today(monkeypatch, MON)
    monkeypatch.setattr(ss, "TEMPLATE_KICK_WAIT_SECONDS", 0.05)
    news = FakeNews({"ceo_buys": cands("ceo_buys", ceo_week())})
    news.gate = asyncio.Event()
    svc = _svc(runs, news)
    rid = _run(sb, MON)
    first = await svc.kick(rid, claim=_claim(sb, rid))
    assert first == {"status": "generating", "source_ref": None, "template_id": None}
    assert sb.tables[mrs.SCRIPTS].rows == [] and rid in svc._builds
    second = await svc.kick(rid, claim=_claim(sb, rid))
    assert second["status"] == "generating" and len(news.calls) == 1          # joined, not rebuilt
    news.gate.set()
    await _drain(svc)
    assert rid not in svc._builds and len(sb.tables[mrs.SCRIPTS].rows) == 1
    done = await svc.kick(rid, claim=_claim(sb, rid))
    assert done["status"] == "accepted" and len(news.calls) == 1


@pytest.mark.asyncio
async def test_a_build_that_crashes_raises_into_the_kick_and_the_next_kick_builds_afresh(tworld, monkeypatch):
    sb, runs = tworld
    _today(monkeypatch, MON)
    news = FakeNews({"ceo_buys": cands("ceo_buys", ceo_week())})

    class Blip(mrs.MarketingRunService):
        fail = 1

        async def recent_source_refs(self, before, limit):
            if Blip.fail:
                Blip.fail -= 1
                raise mrs.MarketingRunError("recent_source_refs failed: 520")
            return await super().recent_source_refs(before, limit)

    svc = _svc(Blip(supabase=sb), news)
    rid = _run(sb, MON)
    with pytest.raises(mrs.MarketingRunError):
        await svc.kick(rid, claim=_claim(sb, rid))
    assert classify_exception(mrs.MarketingRunError("x"))[1] == 503             # the worker retries
    await _drain(svc)
    assert svc._builds == {}
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "accepted" and len(news.calls) == 1


@pytest.mark.asyncio
async def test_two_service_instances_race_one_insert_wins_and_the_other_adopts_it(tworld, monkeypatch):
    """A deploy overlap: two processes build the same day. The build holds no lease; the INSERT is first
    write wins, and the loser answers the winner's row."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    news_a = FakeNews({"ceo_buys": cands("ceo_buys", ceo_week())})
    news_b = FakeNews({"ceo_buys": None, "insider_buys": None, "money_map": cands("money_map", costco())})
    gate = asyncio.Event()
    news_a.gate = news_b.gate = gate          # both builds are past their "no row yet" read before either writes
    a, b = _svc(runs, news_a), _svc(mrs.MarketingRunService(supabase=sb), news_b)
    rid = _run(sb, MON)
    ka = asyncio.create_task(a.kick(rid, claim=_claim(sb, rid)))
    kb = asyncio.create_task(b.kick(rid, claim=_claim(sb, rid)))
    for _ in range(500):
        if news_a.calls and news_b.calls:
            break
        await asyncio.sleep(0.005)
    assert news_a.calls and news_b.calls and sb.tables[mrs.SCRIPTS].rows == []
    gate.set()
    sa, sb_state = await asyncio.gather(ka, kb)
    (row,) = sb.tables[mrs.SCRIPTS].rows
    assert sa["source_ref"] == sb_state["source_ref"] == row["source_ref"]
    assert sa["status"] == sb_state["status"] == "accepted"
    assert {len(news_a.calls), len(news_b.calls)} <= {1, 3}         # each built its own day; one INSERT won


@pytest.mark.asyncio
async def test_a_kick_racing_a_build_that_just_finished_adopts_its_row_without_a_second_walk(
        tworld, monkeypatch, caplog):
    """Review PW-2: kick 2's "no row yet" read returns None, and while it is in flight the running build
    INSERTs, completes and leaves `_builds`. Kick 2 then starts a NEW build — which must read the day's
    row first and adopt it, never walk the adapter (FMP) again only to lose its INSERT with 23505."""
    sb, runs_base = tworld
    _today(monkeypatch, MON)
    monkeypatch.setattr(ss, "TEMPLATE_KICK_WAIT_SECONDS", 0.05)
    news = FakeNews({"ceo_buys": cands("ceo_buys", ceo_week())})
    news.gate = asyncio.Event()
    holder: Dict[str, Any] = {}

    class Racing(mrs.MarketingRunService):
        armed = False

        async def get_script(self, run_id):
            row = await super().get_script(run_id)          # the read answers "no row yet" …
            if Racing.armed:
                Racing.armed = False
                svc, rid_ = holder["svc"], holder["rid"]
                task = svc._builds[rid_]
                news.gate.set()                              # … while the running build lands its row
                await asyncio.wait({task})
                for _ in range(20):
                    if rid_ not in svc._builds:
                        break
                    await asyncio.sleep(0)
                assert rid_ not in svc._builds and len(sb.tables[mrs.SCRIPTS].rows) == 1
            return row

    svc = _svc(Racing(supabase=sb), news)
    rid = _run(sb, MON)
    holder.update(svc=svc, rid=rid)
    first = await svc.kick(rid, claim=_claim(sb, rid))
    assert first["status"] == "generating" and len(news.calls) == 1
    Racing.armed = True
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        second = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert second["status"] == "accepted" and second["template_id"] == "ceo_buys"
    assert len(news.calls) == 1                                      # the adapter was walked ONCE
    assert len(sb.tables[mrs.SCRIPTS].rows) == 1
    assert any("finished build's row ADOPTED" in r.getMessage() and rid in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_shutdown_cancels_a_running_build_and_nothing_is_written(tworld, monkeypatch, caplog):
    sb, runs = tworld
    _today(monkeypatch, MON)
    monkeypatch.setattr(ss, "TEMPLATE_KICK_WAIT_SECONDS", 0.05)
    news = FakeNews({"ceo_buys": cands("ceo_buys", ceo_week())})
    news.gate = asyncio.Event()
    svc = _svc(runs, news)
    rid = _run(sb, MON)
    assert (await svc.kick(rid, claim=_claim(sb, rid)))["status"] == "generating"
    (task,) = list(svc._builds.values())
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.shutdown(timeout=1.0)
        await asyncio.sleep(0)
    assert task.cancelled() and svc._builds == {} and not svc._tasks
    assert sb.tables[mrs.SCRIPTS].rows == []
    assert any("CANCELLED" in r.getMessage() for r in caplog.records)


# ══ the timing constants, pinned against the worker ═══════════════════════════════════════════════


def _worker_constant(name: str) -> float:
    """A numeric module constant of marketing/main.py, read by AST (the worker must not be imported by the
    web side's tests' module state): literals and their + - * / combinations only."""
    tree = ast.parse((_BACKEND / "marketing" / "main.py").read_text(encoding="utf-8"))

    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = value(node.left), value(node.right)
            return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b, ast.Div: a / b if b else float("nan")}[type(node.op)]
        raise AssertionError(f"{name} is not a numeric literal expression")

    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == name for t in targets) and node.value is not None:
            return value(node.value)
    raise AssertionError(f"marketing/main.py defines no {name}")


def test_the_template_timing_constants_fit_the_workers_own():
    backend_timeout = _worker_constant("BACKEND_TIMEOUT_SECONDS")
    poll_budget = _worker_constant("SCRIPT_POLL_BUDGET_SECONDS")
    assert (backend_timeout, poll_budget) == (30.0, 900.0)      # the values the contract was written against
    # one kick never outlives the worker's HTTP timeout (it would be retried while still waiting)
    assert ss.TEMPLATE_KICK_WAIT_SECONDS < backend_timeout - 5
    # the whole build ends inside the worker's script poll
    assert ss.TEMPLATE_BUILD_BUDGET_SECONDS < poll_budget
    assert all(0 < v <= ss.TEMPLATE_BUILD_BUDGET_SECONDS for v in ss.SERIES_BUDGET_SECONDS.values())
    assert 0 < ss.DEFAULT_SERIES_BUDGET_SECONDS <= ss.TEMPLATE_BUILD_BUDGET_SECONDS
    assert ss.SERIES_MIN_START_SECONDS < ss.DEFAULT_SERIES_BUDGET_SECONDS
    assert ss.LOGO_FETCH_TIMEOUT_SECONDS < ss.TEMPLATE_BUILD_BUDGET_SECONDS
    assert set(ss.SERIES_BUDGET_SECONDS) <= set(selection.SERIES_BY_ID)
    assert ss.NEWS_RECENT_LIMIT == ss.RECENT_LIMIT * len(selection.POST_WEEKDAYS) == 240
    assert ss.SERIES_TRAIL_MAX == mrs.SERIES_TRAIL_MAX == 8


# ══ the default classes ("A") are byte-identical; the rotation never sees news refs ═════════════


@pytest.mark.parametrize("classes", ["A", "", "a", "X,Y"])
@pytest.mark.asyncio
async def test_with_class_a_only_every_day_selects_exactly_as_drop_1(tworld, monkeypatch, classes):
    """Two years of dates: the INSERTed row is exactly what Drop 1's `_select` wrote — the same keys, the
    same values — and the company-news source is never touched (the adapter is never imported)."""
    sb, runs = tworld
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", classes)

    def no_news():
        raise AssertionError("class A only: the adapter must never be loaded")

    pool = content_pool.eligible_keys()
    day, end = date(2026, 9, 21), date(2028, 9, 21)
    seen = 0
    while day < end:
        if day.toordinal() % 3:                     # a third of the days keeps the test quick
            day += timedelta(days=1)
            continue
        svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
        monkeypatch.setattr(svc, "_news_source_fn", no_news)
        recent = await runs.recent_source_refs(day, ss.RECENT_LIMIT)
        want = selection.choose(pool, day, recent)
        row = await svc._select({"id": str(uuid.uuid4()), "run_date": day.isoformat()})
        if want.rest_day:
            assert {k: row[k] for k in ("status", "run_date")} == {"status": "rest_day", "run_date": day.isoformat()}
            assert row.get("source_ref") is None and row.get("template_id") is None
        else:
            item = content_pool.get_item(want.source_ref)
            assert (row["status"], row["source_ref"], row["template_id"]) == ("selected", want.source_ref,
                                                                             want.template_id)
            assert row["fact_sheet"] == ss._fact_sheet_snapshot(item)          # no selection block
            seen += 1
        day += timedelta(days=1)
    assert seen > 100


@pytest.mark.asyncio
async def test_news_refs_never_crowd_the_lesson_rotation(tworld, monkeypatch):
    """Three news days per lesson day in the ledger: 34 consecutive lessons are still distinct. The
    rotation reads NEWS_RECENT_LIMIT rows and keeps only `lesson_refs`; with the old 60-row unfiltered
    read the news refs would push lessons out of its window and the pool would repeat."""
    sb, runs = tworld
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    pool = content_pool.eligible_keys()
    assert len(pool) >= 34
    day = SAT
    lessons: List[str] = []
    for i in range(34):
        svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
        row = await svc._select_lesson({"id": str(uuid.uuid4())}, day,
                                       await runs.recent_source_refs(day, ss.NEWS_RECENT_LIMIT),
                                       classes=frozenset({"A", "C", "F"}), selection_block=None)
        lessons.append(row["source_ref"])
        for k in range(3):     # the three news days after it
            _seed_recent(sb, day + timedelta(days=k + 1), f"news:money_map:S{i}{k}:2025")
        day += timedelta(days=7)
    assert len(set(lessons)) == 34


@pytest.mark.asyncio
async def test_a_saturday_with_news_on_is_the_lesson_on_a_weekly_lesson_template(tworld, monkeypatch):
    sb, runs = tworld
    _today(monkeypatch, SAT)

    def no_news():
        raise AssertionError("a lesson-only plan never loads the adapter")

    svc = _svc(runs, None)
    monkeypatch.setattr(svc, "_news_source_fn", no_news)
    rid = _run(sb, SAT)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "generating" and state["template_id"] in selection.LESSON_TEMPLATE_IDS
    assert "selection" not in _script(sb, rid)["fact_sheet"]       # the plan had no news series
    await _drain(svc)


@pytest.mark.asyncio
async def test_a_news_day_whose_classes_are_off_is_the_lesson_with_no_trail(tworld, monkeypatch):
    """Classes A,C on a Thursday (Money Map is F): the enabled chain is just the lesson."""
    sb, runs = tworld
    _today(monkeypatch, THU)
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C")
    news = FakeNews({"money_map": cands("money_map", costco())})
    svc = _svc(runs, news)
    rid = _run(sb, THU)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["template_id"] in selection.LESSON_TEMPLATE_IDS and news.calls == []
    await _drain(svc)


@pytest.mark.parametrize("caps", [["post_image"], [], ["news_templates"]],
                         ids=["drop1_worker", "older_worker", "no_post_image"])
@pytest.mark.asyncio
async def test_a_news_day_held_by_a_worker_that_cannot_draw_a_template_is_the_lesson(tworld, monkeypatch, caplog,
                                                                                    caps):
    """Review PW-1 / boundary F1: a run held by a worker that did not declare `news_templates` (a drop-1
    image — a rollback, or the web deployed first) would draw a template as a LESSON: the person's name on
    the video's first frame (the platforms' cover), the alt text on the image. Its news day is the lesson
    instead — never a template, never a failed day — and the WARNING names the worker."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    news = FakeNews({"ceo_buys": cands("ceo_buys", ceo_week())})
    svc = _svc(runs, news)
    rid = _run(sb, MON, caps=caps)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    if "news_templates" in caps:          # the capability alone decides the chain
        assert state["template_id"] == "ceo_buys" and len(news.calls) == 1
        return
    assert state["template_id"] in selection.LESSON_TEMPLATE_IDS and news.calls == [] and svc._builds == {}
    row = _script(sb, rid)
    assert row["template_id"] in selection.LESSON_TEMPLATE_IDS and "selection" not in (row["fact_sheet"] or {})
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
              and "did not declare 'news_templates'" in r.getMessage()]
    assert len(warned) == 1 and rid in warned[0] and "'drop2a-test'" in warned[0] and "LESSON" in warned[0]


@pytest.mark.asyncio
async def test_a_template_frozen_under_a_holder_without_news_templates_is_text(tworld, caplog):
    """The holder can change between the selection and the freeze (a re-claim during the build). A
    template's image is its `image_spec`, which only a `news_templates` worker draws: under any other
    holder its platforms freeze as TEXT (a lesson still needs only `post_image`)."""
    sb, runs = tworld
    svc = _svc(runs, FakeNews({}))
    out = _composed()
    lesson = {"posts": {"bluesky": {"caption": "c"}}, "image_post": {"title": "A title", "paragraphs": ["One.", "Two."]}}
    for caps, template_image, lesson_image in ((DROP2_CAPS, True, True), (["post_image"], False, True),
                                               ([], False, False)):
        rid = _run(sb, MON, caps=caps)
        with caplog.at_level(logging.WARNING, logger=ss.logger.name):
            frozen = await svc._frozen_output(out, MON, run_id=rid, gen_id="g", image_posts=True)
            plain = await svc._frozen_output(lesson, MON, run_id=rid, gen_id="g", image_posts=True)
        assert ("image" in frozen["post_formats"].values()) is template_image, caps
        assert frozen["image_footer"] == out["image_footer"]                      # the template's own, always
        assert (plain["post_formats"]["bluesky"] == "image") is lesson_image, caps
    assert any("'news_templates'" in r.getMessage() and "drop-2" in r.getMessage() for r in caplog.records)


def test_the_2b_layout_series_are_exactly_the_pair_and_grid_series():
    """`LAYOUTS_2B_SERIES` is derived from the templates' own specs; the capability's layouts are real,
    shipped layouts that drop 2a did not ship (rows / spotlight / bars stay drawable by a 2a worker)."""
    from app.schemas.marketing import WORKER_CAPABILITIES, WORKER_CAPABILITY_LAYOUTS_2B, WORKER_LAYOUTS_2B

    assert WORKER_CAPABILITY_LAYOUTS_2B == "layouts_2b" and WORKER_CAPABILITY_LAYOUTS_2B in WORKER_CAPABILITIES
    assert set(WORKER_LAYOUTS_2B) == {"pair", "grid"} and set(WORKER_LAYOUTS_2B) <= set(onscreen.SHIPPED_LAYOUTS)
    assert ss.LAYOUTS_2B_SERIES == {"company_stakes", "theme_explainer"}
    assert ss.LAYOUTS_2B_SERIES <= selection.SHIPPED_SERIES


@pytest.mark.parametrize("series", ["company_stakes", "theme_explainer"])
@pytest.mark.asyncio
async def test_a_2b_layout_series_leaves_the_chain_of_a_holder_without_layouts_2b(tworld, monkeypatch, caplog,
                                                                                   series):
    """Review R9 (critic): a drop-2a worker image declares `news_templates` but cannot draw a `pair` /
    `grid` image — handed one, it refuses it and the day FAILS. Under such a holder the series whose image
    is a 2b layout leave the day's chain (WARNING naming the worker and the capability); the rest of the
    chain runs as usual and falls through as for any series that yields nothing. With `layouts_2b`
    declared, the same day reaches the series (test_a_2b_series_listed_in_the_switch_builds_its_day…)."""
    sb, runs = tworld
    rec, day, _klass, layout = _2b_case(series)
    assert layout in ("pair", "grid")
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    _today(monkeypatch, day)
    news = FakeNews({series: cands(series, rec)})
    svc = _svc(runs, news)
    rid = _run(sb, day, caps=DROP2A_CAPS)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_claim(sb, rid))
        await _drain(svc)
    full = selection.enabled_chain(selection.plan_for(day).chain, frozenset({"A", "C", "F"}),
                                   shipped=selection.parse_news_series(EVERY_SERIES))
    assert series in full                                    # the day's chain really had the series
    kept = [s for s in full if s not in ss.LAYOUTS_2B_SERIES]
    asked = [c["series"] for c in news.calls]
    assert asked == [s for s in kept if s != selection.LESSON], asked
    row = _script(sb, rid)
    assert row["template_id"] != series and row["template_id"] in selection.LESSON_TEMPLATE_IDS
    if kept != [selection.LESSON]:
        assert row["fact_sheet"]["selection"]["chain"] == kept
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
              and "did not declare 'layouts_2b'" in r.getMessage()]
    assert len(warned) == 1 and rid in warned[0] and series in warned[0] and "drop-2b" in warned[0], warned


@pytest.mark.asyncio
async def test_a_chain_of_only_2b_layout_series_under_a_holder_without_layouts_2b_is_the_lesson(tworld, monkeypatch,
                                                                                               caplog):
    """The fall-through's end: when the switch lists only the two 2b-layout series, a drop-2a holder's chain
    is just the lesson — selected at once, the adapter never asked, no fallback trail — never a failed day."""
    sb, runs = tworld
    rec, day, _klass, _layout = _2b_case("company_stakes")
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "company_stakes,theme_explainer")
    _today(monkeypatch, day)
    news = FakeNews({"company_stakes": cands("company_stakes", rec)})
    svc = _svc(runs, news)
    rid = _run(sb, day, caps=DROP2A_CAPS)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_claim(sb, rid))
        await _drain(svc)
    assert news.calls == [] and svc._builds == {}
    row = _script(sb, rid)
    assert row["template_id"] in selection.LESSON_TEMPLATE_IDS and "selection" not in (row["fact_sheet"] or {})
    assert len([r for r in caplog.records if "did not declare 'layouts_2b'" in r.getMessage()]) == 1


@pytest.mark.asyncio
async def test_a_holder_without_layouts_2b_loses_nothing_on_a_day_without_a_2b_layout_series(tworld, monkeypatch,
                                                                                            caplog):
    """No over-block, and nothing logged: with the switch at its default (no 2b series) a drop-2a holder's
    Monday asks for exactly the series a drop-2b holder's does."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    asked = {}
    for caps in (DROP2A_CAPS, DROP2_CAPS):
        news = FakeNews({})
        svc = _svc(runs, news)
        rid = _run(sb, MON, caps=caps)
        with caplog.at_level(logging.WARNING, logger=ss.logger.name):
            await svc.kick(rid, claim=_claim(sb, rid))
            await _drain(svc)
        asked[tuple(caps)] = [c["series"] for c in news.calls]
    assert asked[tuple(DROP2A_CAPS)] == asked[tuple(DROP2_CAPS)] and asked[tuple(DROP2_CAPS)]
    assert not [r for r in caplog.records if "layouts_2b" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_2b_layout_template_frozen_under_a_holder_without_layouts_2b_is_text(tworld, caplog):
    """The freeze half (a re-claim by a drop-2a image during the build): a `pair` / `grid` template's
    platforms freeze as TEXT under a holder without `layouts_2b` — it draws the video, never the image it
    would refuse — and as IMAGE under the drop-2b worker. A 2a-layout template keeps its image."""
    sb, runs = tworld
    svc = _svc(runs, FakeNews({}))
    for series in ("company_stakes", "theme_explainer"):
        rec, day, _klass, layout = _2b_case(series)
        out = _composed(rec, day)
        assert out["image_spec"]["layout"] == layout
        for caps, image in ((DROP2_CAPS, True), (DROP2A_CAPS, False)):
            rid = _run(sb, day, caps=caps)
            with caplog.at_level(logging.WARNING, logger=ss.logger.name):
                frozen = await svc._frozen_output(out, day, run_id=rid, gen_id="g", image_posts=True)
            assert ("image" in frozen["post_formats"].values()) is image, (series, caps)
            assert frozen["image_footer"] == out["image_footer"]
    rid = _run(sb, MON, caps=DROP2A_CAPS)
    frozen = await svc._frozen_output(_composed(), MON, run_id=rid, gen_id="g", image_posts=True)
    assert "image" in frozen["post_formats"].values()                       # `rows`: a 2a worker draws it
    assert any("'layouts_2b'" in r.getMessage() and "drop-2b" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_selected_row_naming_a_news_series_is_rejected_source_ineligible(tworld, monkeypatch):
    """Pinned, no new code: a `selected` row is only ever a lesson's. One that names a series (a hand
    edit) can never reach the writer — `_generate_owned` rejects it `source_ineligible`."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    writer = FakeWriter(["accepted"])
    svc = _svc(runs, FakeNews({}), writer)
    rid = _run(sb, MON)
    sb.tables[mrs.SCRIPTS].rows.append({"run_id": rid, "run_date": MON.isoformat(), "status": "selected",
                                        "source_ref": "news:ceo_buys:2026-11-09", "template_id": "ceo_buys",
                                        "generations": 0, "content_rejections": 0, "violations": [],
                                        "fact_sheet": {}, "tokens_used": 0})
    await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    row = _script(sb, rid)
    assert row["status"] == "rejected" and row["reject_reason"] == "source_ineligible" and writer.calls == []


# ══ the run mirror ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_mirror_write_is_fenced_and_never_reverts_a_claim_that_lands_under_it(tworld, monkeypatch):
    """The metadata merge is read-then-write. A re-claim landing between `update_run`'s read and its
    UPDATE (a new attempts + nonce) must not be overwritten with the stale copy — that would lock the
    new holder out. The fenced write matches nothing; the next kick (of the new holder) heals it."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    news = FakeNews({"ceo_buys": cands("ceo_buys", ceo_week())})
    svc = _svc(runs, news)
    rid = _run(sb, MON)
    fired = {"n": 0}

    def reclaim(payload):
        if "source_ref" in payload and not fired["n"]:
            fired["n"] += 1
            row = _run_row(sb, rid)
            row["attempts"] = 2
            row["metadata"] = {**row["metadata"], "claim_nonce": "ab" * 16}
            row["updated_at"] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()

    sb.tables[mrs.RUNS].before_update = reclaim
    await svc.kick(rid, claim=_claim(sb, rid))
    run = _run_row(sb, rid)
    assert fired["n"] == 1 and run["metadata"]["claim_nonce"] == "ab" * 16 and run["attempts"] == 2
    assert run.get("source_ref") is None                              # nothing written over it
    sb.tables[mrs.RUNS].before_update = None
    await svc.kick(rid, claim=_claim(sb, rid))                       # the new holder's poll heals it
    run = _run_row(sb, rid)
    assert run["source_ref"] == "news:ceo_buys:2026-11-09" and run["metadata"]["claim_nonce"] == "ab" * 16


@pytest.mark.asyncio
async def test_an_unknown_template_mirrors_no_class(tworld, monkeypatch, caplog):
    sb, runs = tworld
    _today(monkeypatch, MON)
    svc = _svc(runs, FakeNews({}))
    rid = _run(sb, MON, content_class="A")
    sb.tables[mrs.SCRIPTS].rows.append({"run_id": rid, "run_date": MON.isoformat(), "status": "rejected",
                                        "reject_reason": "content", "source_ref": "news:x:1",
                                        "template_id": "retired_series", "fact_sheet": {}})
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_claim(sb, rid))
    run = _run_row(sb, rid)
    assert run["source_ref"] == "news:x:1" and run["template_id"] == "retired_series" and run["content_class"] == "A"
    assert any("has no content class" in r.getMessage() for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_claim(sb, rid))                 # all that can match matches: no rewrite
    assert not [r for r in caplog.records if "has no content class" in r.getMessage()]


# ══ freeze_post_formats and worker_script: the template branches ═════════════════════════════════


def _composed(rec=None, run_date=MON) -> Dict[str, Any]:
    out = news_templates.compose(rec or ceo_week(run_date), run_date=run_date, store_state="live", allow_x_url=False)
    out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None,
                     "width": None, "height": None} for r in news_templates.logo_refs(out)]
    return out


@pytest.mark.parametrize("image_posts", [True, False])
def test_freeze_keeps_the_template_footer_whatever_the_formats(image_posts):
    out = _composed()
    frozen = ss.freeze_post_formats(out, MON, image_posts=image_posts, x_images=False, run_id="r")
    assert frozen["image_footer"] == out["image_footer"]
    assert ("image" in frozen["post_formats"].values()) is image_posts
    assert news_templates.revalidate(json.loads(json.dumps(frozen)), fact_sheet=_sheet(ceo_week()), run_date=MON) == []


def test_freeze_freezes_a_template_whose_image_spec_does_not_validate_as_text(caplog):
    out = _composed()
    out["image_spec"] = {**out["image_spec"], "layout": "table"}          # no such layout
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        frozen = ss.freeze_post_formats(out, MON, image_posts=True, x_images=True, run_id="r")
    assert "image" not in frozen["post_formats"].values() and frozen["image_footer"] == out["image_footer"]
    assert any("image_spec does not validate" in r.getMessage() for r in caplog.records)
    # a spec referencing a logo the run carries no entry for is not drawable either
    out = _composed()
    out["logos"] = out["logos"][1:]
    assert "image" not in ss.freeze_post_formats(out, MON, image_posts=True, x_images=True)["post_formats"].values()


@pytest.mark.parametrize("footer", [None, "", "  ", 3])
def test_freeze_refuses_a_template_without_its_footer(footer):
    out = _composed()
    out["image_footer"] = footer
    with pytest.raises(ValueError, match="no image_footer"):
        ss.freeze_post_formats(out, MON, image_posts=True, x_images=False)


def test_a_lesson_freezes_exactly_as_drop_1():
    pkg = {"posts": {"bluesky": {"caption": "c"}, "tiktok": {"caption": "c"}},
           "image_post": {"title": "A title", "paragraphs": ["One.", "Two."]}}
    out = ss.freeze_post_formats(pkg, MON, image_posts=True, x_images=False)
    assert out["post_formats"] == {"bluesky": "image", "tiktok": "video"}
    assert out["image_footer"] == post_copy.image_footer(MON)
    assert ss.freeze_post_formats({**pkg, "image_footer": "stale"}, MON, image_posts=False,
                                  x_images=False).get("image_footer") is None


@pytest.mark.asyncio
async def test_a_failed_freeze_keeps_a_templates_footer(tworld, monkeypatch, caplog):
    sb, runs = tworld

    def boom(*_a, **_k):
        raise RuntimeError("freeze bug")

    monkeypatch.setattr(ss, "freeze_post_formats", boom)
    svc = _svc(runs, FakeNews({}))
    out = _composed()
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        stored = await svc._frozen_output(out, MON, run_id="r", gen_id="g", image_posts=False)
    assert "post_formats" not in stored and stored["image_footer"] == out["image_footer"]
    lesson = await svc._frozen_output({"posts": {}, "image_footer": "x"}, MON, run_id="r", gen_id="g",
                                      image_posts=False)
    assert "image_footer" not in lesson


def test_worker_script_sends_only_validated_template_fields(caplog):
    out = ss.freeze_post_formats(_composed(), MON, image_posts=True, x_images=False)
    good = ss.worker_script(out)
    assert good["opening_card"] == out["opening_card"] and good["image_spec"] == out["image_spec"]
    assert good["video_layout"] == "per_line" and good["series"] == "ceo_buys"
    assert [set(lg) for lg in good["logos"]] == [set(ss._WORKER_LOGO_KEYS)] * len(out["logos"])
    bad = copy.deepcopy(out)
    bad["opening_card"]["logos"] = ["ZZZZ"]                        # a logo the run carries no entry for
    bad["image_spec"]["kicker"] = "https://evil.example/x"         # never drawable
    bad["video_layout"] = "grid"
    bad["series"] = "not_a_series"
    bad["content_class"] = "Z"
    bad["authorship"] = "robot"
    bad["logos"] = [*bad["logos"], {"key": "A" * 17, "name": "Too Long Key"}, {"key": "X", "name": "X",
                                                                               "sha256": "nothex"}, "junk"]
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        sent = ss.worker_script(bad)
    assert (sent["opening_card"], sent["image_spec"], sent["video_layout"], sent["series"],
            sent["content_class"], sent["authorship"]) == (None, None, None, None, None, None)
    assert [lg["key"] for lg in sent["logos"]] == [lg["key"] for lg in out["logos"]]
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) >= 8
    WorkerScript.model_validate(sent)


def test_worker_script_caps_the_logos_and_strips_unknown_keys():
    out = {"logos": [{"key": f"K{i}", "name": f"Company {i}", "url": None, "sha256": None, "extra": "x"}
                     for i in range(20)]}
    sent = ss.worker_script(out)["logos"]
    assert len(sent) == onscreen.MAX_LOGOS and all("extra" not in lg for lg in sent)


# ══ logos: fetch → check → store, never refusing a candidate ═════════════════════════════════════


@pytest.mark.asyncio
async def test_logos_are_attached_in_draw_order_and_every_failure_is_a_wordmark(tworld, monkeypatch, caplog):
    sb, runs = tworld
    out = _composed()
    keys = [r["key"] for r in news_templates.logo_refs(out)]
    assert keys == ["LOW", "SBUX"]
    news = FakeNews({}, logos={"LOW": (b"GIF89a", "image/gif"), "SBUX": None})
    svc = _svc(runs, news)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        logos = await svc._attach_logos(out, deadline=ss._mono() + 60, news=news, run_id="r")
    assert [lg["key"] for lg in logos] == keys and all(lg["url"] is None for lg in logos)
    assert any("refused" in r.getMessage() for r in caplog.records)
    # a fetch that raises, and a store outage: wordmarks too
    news = FakeNews({}, logos={"LOW": RuntimeError("socket"), "SBUX": (png(), "image/png")})
    sb.storage.fail_upload = RuntimeError("storage 503")
    logos = await _svc(runs, news)._attach_logos(out, deadline=ss._mono() + 60, news=news, run_id="r")
    assert all(lg["url"] is None for lg in logos)
    # no time left: no fetch at all
    news = FakeNews({})
    logos = await _svc(runs, news)._attach_logos(out, deadline=ss._mono() - 1, news=news, run_id="r")
    assert news.logo_calls == [] and all(lg["url"] is None for lg in logos)


@pytest.mark.asyncio
async def test_a_logo_is_stored_once_content_addressed_and_reused(tworld):
    sb, runs = tworld
    data = png(160, 120)
    info = logo_check.inspect_logo(data, "image/png")
    entry = await runs.store_logo(data, info)
    path = f"logos/{info.sha256[:32]}.png"
    assert entry == {"path": path, "url": f"{_SB_PUBLIC}/storage/v1/object/public/marketing-media/{path}",
                     "sha256": info.sha256, "bytes": len(data), "width": 160, "height": 120}
    ((up_path, opts),) = sb.storage.uploads
    assert up_path == path and opts == {"content-type": "image/png", "cache-control": "31536000", "upsert": "false"}
    again = await runs.store_logo(data, info)
    assert again == entry and len(sb.storage.uploads) == 1            # reused, never re-uploaded


@pytest.mark.asyncio
async def test_a_logo_key_holding_another_object_is_never_overwritten_or_deleted(tworld, caplog):
    sb, runs = tworld
    data = png()
    info = logo_check.inspect_logo(data, "image/png")
    path = f"logos/{info.sha256[:32]}.png"
    sb.storage.objects[path] = (b"x" * 10, "image/png")
    with caplog.at_level(logging.ERROR, logger=mrs.logger.name):
        assert await runs.store_logo(data, info) is None
    assert sb.storage.uploads == [] and sb.storage.removed == [] and sb.storage.objects[path] == (b"x" * 10, "image/png")
    assert any("already holds another object" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_concurrent_store_of_the_same_logo_is_adopted_and_a_different_one_is_not(tworld):
    sb, runs = tworld
    data = png()
    info = logo_check.inspect_logo(data, "image/png")
    sb.storage.race = (data, "image/png")
    assert (await runs.store_logo(data, info))["sha256"] == info.sha256
    other = png(shade=99)
    info2 = logo_check.inspect_logo(other, "image/png")
    sb.storage.race = (b"y" * 7, "image/png")
    assert await runs.store_logo(other, info2) is None


@pytest.mark.asyncio
async def test_store_logo_refuses_what_it_cannot_vouch_for(tworld):
    sb, runs = tworld
    data = png()
    info = logo_check.inspect_logo(data, "image/png")
    assert await runs.store_logo(png(shade=3), info) is None                    # bytes ≠ the inspected logo
    assert await runs.store_logo(b"", info) is None
    assert await runs.store_logo(data, {"sha256": info.sha256}) is None         # not a LogoInfo
    sb.storage.fail_list = RuntimeError("storage list 503")
    assert await runs.store_logo(data, info) is None                            # cannot stat: never blind
    assert sb.storage.uploads == []


# ══ create_posts: the template branch, end to end on a real composed output ═════════════════════


def _sheet(rec) -> Dict[str, Any]:
    return json.loads(json.dumps(R.fact_sheet(rec, rejections={}, selection={
        "plan": "monday", "chain": [rec.series, "lesson"], "trail": [{"series": rec.series, "outcome": "chosen"}]})))


async def _template_run(sb, runs, *, rec=None, run_date=MON, logos_stored: bool = True,
                        tamper=None) -> str:
    rec = rec or ceo_week(run_date)
    out = news_templates.compose(rec, run_date=run_date, store_state="live", allow_x_url=False)
    out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None,
                     "width": None, "height": None} for r in news_templates.logo_refs(out)]
    out = ss.freeze_post_formats(out, run_date, image_posts=True, x_images=False, run_id="r")
    out = json.loads(json.dumps(out))
    if tamper is not None:
        tamper(out)
    rid = _run(sb, run_date)
    sb.tables[mrs.SCRIPTS].rows.append({
        "run_id": rid, "run_date": run_date.isoformat(), "status": "accepted", "source_ref": R.ledger_key(rec),
        "template_id": rec.series, "output": out, "fact_sheet": _sheet(rec), "generation_id": "g",
        "generations": 0, "tokens_used": 0, "violations": []})
    return rid


def _ready(sb, rid, kind, *, metadata=None, image_role=None) -> str:
    aid = str(uuid.uuid4())
    md = dict(metadata or {})
    if image_role:
        md["image_role"] = image_role
    sb.tables[mrs.ASSETS].rows.append({"id": aid, "run_id": rid, "kind": kind, "status": "ready",
                                       "storage_path": f"x/{aid}", "metadata": md})
    return aid


@pytest.mark.parametrize("auto", [True, False])
@pytest.mark.asyncio
async def test_template_posts_are_recorded_pending_review_with_their_metadata(tworld, monkeypatch, auto):
    sb, runs = tworld
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", auto)
    rid = await _template_run(sb, runs)
    video = _ready(sb, rid, "video")
    card = _ready(sb, rid, "card", image_role="post_image")
    run = _run_row(sb, rid)
    run["metadata"].update({"video_asset_id": video, "image_asset_id": card})
    output = _script(sb, rid)["output"]
    specs = [{"platform": "x", "format": "text"}, {"platform": "tiktok", "format": "video", "asset_ids": [video]},
             {"platform": "bluesky", "format": "image", "asset_ids": [card]}]
    assert output["post_formats"]["bluesky"] == "image" and output["post_formats"]["x"] == "text"
    posts = await runs.create_posts(rid, specs, claim=_claim(sb, rid))
    assert {p["status"] for p in posts} == {"pending_review"}           # never auto-approved
    flags = {p["platform"]: p["metadata"]["made_with_ai"] for p in posts}
    assert flags == {"x": False, "tiktok": True, "bluesky": False}
    for p in posts:
        md = p["metadata"]
        assert (md["content_class"], md["series"], md["authorship"]) == ("C", "ceo_buys", "template")
        assert md["series_trail"] == [{"series": "ceo_buys", "outcome": "chosen"}]
        assert p["caption"] == output["posts"][p["platform"]]["caption"]


@pytest.mark.parametrize("what, tamper", [
    ("a caption", lambda o: o["posts"]["x"].__setitem__("caption", o["posts"]["x"]["caption"] + " Buy now.")),
    ("the hook", lambda o: o.__setitem__("hook", o["hook"].replace("disclosed", "said"))),
    ("an image string", lambda o: o["image_spec"].__setitem__("title", "Hot stock picks")),
    ("the version", lambda o: o.__setitem__("template_version", "news-v0")),
    ("the opening card", lambda o: o["opening_card"].__setitem__("headline", "A great buy")),
    ("the footer", lambda o: o.__setitem__("image_footer", o["image_footer"] + " ")),
])
@pytest.mark.asyncio
async def test_a_template_that_fails_its_recheck_is_refused_and_nothing_is_written(tworld, monkeypatch, caplog,
                                                                                  what, tamper):
    sb, runs = tworld
    rid = await _template_run(sb, runs, tamper=tamper)

    async def no_asset_read(run_id):
        raise AssertionError("the re-check refusal comes before any asset read")

    monkeypatch.setattr(runs, "list_assets", no_asset_read)
    before = copy.deepcopy(sb.tables[mrs.POSTS].rows)
    with caplog.at_level(logging.ERROR, logger=mrs.logger.name):
        with pytest.raises(mrs.MarketingTemplateRefused) as ei:
            await runs.create_posts(rid, [{"platform": "x", "format": "text"}], claim=_claim(sb, rid))
    assert classify_exception(ei.value) == (ErrorCode.MARKETING_TEMPLATE_REFUSED, 409)
    assert sb.tables[mrs.POSTS].rows == before == []
    assert any("create_posts TEMPLATE REFUSED" in r.getMessage() and rid in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_template_whose_fact_sheet_was_edited_is_refused(tworld):
    sb, runs = tworld
    rid = await _template_run(sb, runs)
    _script(sb, rid)["fact_sheet"]["record"]["rows"][0]["amount_usd"] = 9_000_000.0
    with pytest.raises(mrs.MarketingTemplateRefused, match="failed its re-check"):
        await runs.create_posts(rid, [{"platform": "x", "format": "text"}], claim=_claim(sb, rid))


@pytest.mark.asyncio
async def test_the_money_map_template_records_as_class_f(tworld):
    sb, runs = tworld
    rid = await _template_run(sb, runs, rec=costco(), run_date=THU)
    (post,) = await runs.create_posts(rid, [{"platform": "x", "format": "text"}], claim=_claim(sb, rid))
    assert (post["metadata"]["content_class"], post["metadata"]["series"]) == ("F", "money_map")


# ══ drop 2b: the four series it ships, through the build and create_posts ════════════════════════
#
# Shipped in code (selection.SHIPPED_SERIES, the `pair` / `grid` layouts on both sides), OFF in
# production until MARKETING_NEWS_SERIES lists them. Records: the templates' own goldens
# (tests/test_marketing_news_templates.py), each on the posting day whose chain reaches its series.

EVERY_SERIES = ",".join(s.id for s in selection.SERIES)


def _2b_case(series: str):
    import test_marketing_news_templates as NT

    return {"congress_count": (NT.congress(), NT.CONGRESS_RUN, "C", "spotlight"),
            "company_stakes": (NT.nscale(), NT.STAKES_RUN, "F", "pair"),
            "earnings": (NT.earnings(), NT.EARNINGS_RUN, "F", "rows"),
            "theme_explainer": (NT.ai_chips(), NT.THEME_RUN, "F", "grid")}[series]


@pytest.mark.parametrize("series", ["congress_count", "company_stakes", "earnings", "theme_explainer"])
@pytest.mark.asyncio
async def test_a_2b_series_listed_in_the_switch_builds_its_day_and_records_its_posts(tworld, monkeypatch, series):
    """Every series listed: the day's build reaches the 2b series (the steps before it come up empty),
    accepts ONE template row whose image freezes as an IMAGE post (its layout validates with the real
    SHIPPED_LAYOUTS — before the flip a stake / theme was refused `image_spec_invalid`), re-checks clean,
    and create_posts records every frozen format pending review, carrying the series. With the switch back
    at its default (2b off), the same accepted day is refused at create_posts (409 → `template_refused`)."""
    from app.config import Settings

    sb, runs = tworld
    rec, day, klass, layout = _2b_case(series)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    _today(monkeypatch, day)
    news = FakeNews({series: cands(series, rec)})
    svc = _svc(runs, news)
    rid = _run(sb, day)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == series
    asked = [c["series"] for c in news.calls]
    assert asked[-1] == series and asked == list(selection.plan_for(day).chain[:len(asked)])
    row = _script(sb, rid)
    out = row["output"]
    assert (row["template_id"], row["tokens_used"], out["content_class"], out["series"]) == (series, 0, klass, series)
    assert out["image_spec"]["layout"] == layout
    assert onscreen.validate_image_spec(out["image_spec"], onscreen.logo_keys(out["logos"]),
                                        footer=out["image_footer"]) is None
    assert "image" in out["post_formats"].values() and "video" in out["post_formats"].values()
    assert news_templates.revalidate(out, fact_sheet=row["fact_sheet"], run_date=day) == []
    assert _run_row(sb, rid)["metadata"]["series"] == series
    video, card = _ready(sb, rid, "video"), _ready(sb, rid, "card", image_role="post_image")
    _run_row(sb, rid)["metadata"].update({"video_asset_id": video, "image_asset_id": card})
    specs = [{"platform": p, "format": f, **({"asset_ids": [video if f == "video" else card]} if f != "text" else {})}
             for p, f in sorted(out["post_formats"].items())]
    # the switch back at its default before the posts: refused, nothing recorded
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", Settings.model_fields["MARKETING_NEWS_SERIES"].default)
    with pytest.raises(mrs.MarketingTemplateRefused, match="MARKETING_NEWS_SERIES"):
        await runs.create_posts(rid, specs, claim=_claim(sb, rid))
    assert sb.tables[mrs.POSTS].rows == []
    # listed again: every frozen format recorded, held for review, carrying the series
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", series)
    posts = await runs.create_posts(rid, specs, claim=_claim(sb, rid))
    assert {(p["platform"], p["format"]) for p in posts} == set(out["post_formats"].items())
    assert {p["status"] for p in posts} == {"pending_review"}
    for p in posts:
        md = p["metadata"]
        assert (md["content_class"], md["series"], md["authorship"]) == (klass, series, "template")
        assert md["made_with_ai"] is (p["format"] == "video")


# ══ the on-screen checks: the template branches ═════════════════════════════════════════════════


def _voice(sb, rid) -> str:
    return _ready(sb, rid, "audio", metadata={"words": [{"w": "x", "s": 0, "e": 1}]})


def _video_strings(out) -> List[str]:
    cards = [c[k] for c in out["cards"] for k in ("title", "body")]
    return [*onscreen.opening_strings(out["opening_card"], out["logos"]), *cards, out["disclaimer_card"],
            VIDEO_BRAND_TEXT[0]]


@pytest.mark.asyncio
async def test_a_template_video_may_draw_its_opening_card_and_nothing_else(tworld):
    sb, runs = tworld
    rid = await _template_run(sb, runs)
    out = _script(sb, rid)["output"]
    run = _run_row(sb, rid)
    voice = _voice(sb, rid)
    drawn = _video_strings(out)
    assert out["opening_card"]["headline"] in drawn and "Lowe's" in drawn         # the wordmark name
    await runs._check_onscreen_text(run, {"onscreen_text": drawn, "voice_asset_id": voice})
    for extra in ("Buy now", f"{_SB_PUBLIC}/storage/v1/object/public/marketing-media/logos/" + "a" * 32 + ".png",
                  out["image_post"]["title"]):
        with pytest.raises(mrs.MarketingRequestInvalid, match="opening card"):
            await runs._check_onscreen_text(run, {"onscreen_text": [*drawn, extra], "voice_asset_id": voice})
    no_disclaimer = [s for s in drawn if s != out["disclaimer_card"]]
    with pytest.raises(mrs.MarketingRequestInvalid, match="disclaimer"):
        await runs._check_onscreen_text(run, {"onscreen_text": no_disclaimer, "voice_asset_id": voice})


@pytest.mark.asyncio
async def test_a_template_video_drawn_as_a_lesson_is_refused(tworld, caplog):
    """Review boundary F1: a worker that renders a template as a LESSON (a drop-1 image ignores
    `video_layout` and `opening_card`) declares only the cards' titles/bodies, the disclaimer and the
    end card — every string allowed, but its first frame is cards[0] (on a Form 4 day, the person's name,
    the platforms' cover). The opening card's kicker and headline MUST be drawn: refused, fail closed."""
    sb, runs = tworld
    rid = await _template_run(sb, runs)
    out = _script(sb, rid)["output"]
    run = _run_row(sb, rid)
    voice = _voice(sb, rid)
    lesson_shape = [*[c[k] for c in out["cards"] for k in ("title", "body")], out["disclaimer_card"],
                    VIDEO_BRAND_TEXT[0]]
    with caplog.at_level(logging.ERROR, logger=mrs.logger.name):
        with pytest.raises(mrs.MarketingRequestInvalid, match="does not draw its opening card"):
            await runs._check_onscreen_text(run, {"onscreen_text": lesson_shape, "voice_asset_id": voice})
    assert any("drew the template as a lesson" in r.getMessage() and rid in r.getMessage() for r in caplog.records)
    # the kicker alone, or the headline alone, is not the opening card either
    for drop in ("kicker", "headline"):
        partial = [s for s in _video_strings(out) if s != out["opening_card"][drop]]
        with pytest.raises(mrs.MarketingRequestInvalid, match="does not draw its opening card"):
            await runs._check_onscreen_text(run, {"onscreen_text": partial, "voice_asset_id": voice})
    # with every logo verified no wordmark name is drawn, and that is still a complete opening card
    no_wordmarks = [s for s in _video_strings(out) if s not in {lg["name"] for lg in out["logos"]}]
    await runs._check_onscreen_text(run, {"onscreen_text": no_wordmarks, "voice_asset_id": voice})


@pytest.mark.parametrize("break_it", [
    lambda o: o.__setitem__("opening_card", None),
    lambda o: o.pop("opening_card"),
    lambda o: o["opening_card"].__setitem__("logos", ["ZZZZ"]),                  # no logo entry of the run
    lambda o: o["opening_card"].__setitem__("verdict", "Strong buy"),            # a key outside the schema
    lambda o: o["opening_card"].__setitem__("headline", "https://evil.example/x"),
    lambda o: o.__setitem__("logos", []),
], ids=["none", "missing", "unknown_logo", "unknown_key", "url_headline", "no_logos"])
@pytest.mark.asyncio
async def test_a_template_whose_opening_card_does_not_validate_allows_no_video(tworld, caplog, break_it):
    sb, runs = tworld
    rid = await _template_run(sb, runs)
    out = _script(sb, rid)["output"]
    drawn = [*[c[k] for c in out["cards"] for k in ("title", "body")], out["disclaimer_card"]]
    break_it(out)
    run = _run_row(sb, rid)
    with caplog.at_level(logging.ERROR, logger=mrs.logger.name):
        with pytest.raises(mrs.MarketingRequestInvalid, match="opening_card does not validate"):
            await runs._check_onscreen_text(run, {"onscreen_text": drawn, "voice_asset_id": _voice(sb, rid)})
    assert any("marketing video REFUSED" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_lesson_video_may_not_draw_an_opening_card(tworld):
    """The opening strings are allowed only on a template (the SCRIPT's class): a lesson keeps Drop 1's
    allow-list exactly."""
    sb, runs = tworld
    rid = await _template_run(sb, runs)
    script = _script(sb, rid)
    out = script["output"]
    script["template_id"] = "checklist"
    out.pop("authorship")
    run = _run_row(sb, rid)
    with pytest.raises(mrs.MarketingRequestInvalid):
        await runs._check_onscreen_text(run, {"onscreen_text": _video_strings(out), "voice_asset_id": _voice(sb, rid)})


@pytest.mark.parametrize("authorship", [None, "ai"])
@pytest.mark.asyncio
async def test_a_template_script_without_template_authorship_has_no_onscreen_allow_list(tworld, authorship):
    sb, runs = tworld
    rid = await _template_run(sb, runs)
    out = _script(sb, rid)["output"]
    out["authorship"] = authorship
    run = _run_row(sb, rid)
    with pytest.raises(mrs.MarketingRequestInvalid, match="authorship"):
        await runs._check_onscreen_text(run, {"onscreen_text": [out["disclaimer_card"]],
                                              "voice_asset_id": _voice(sb, rid)})
    with pytest.raises(mrs.MarketingRequestInvalid, match="authorship"):
        await runs._check_post_image_text(run, {"image_role": "post_image", "onscreen_text": [out["image_footer"]]},
                                          ext="jpg", size_bytes=1000)


@pytest.mark.asyncio
async def test_a_template_post_image_draws_its_spec_and_footer_never_its_alt_text(tworld):
    sb, runs = tworld
    rid = await _template_run(sb, runs)
    out = _script(sb, rid)["output"]
    run = _run_row(sb, rid)
    drawn = onscreen.image_strings(out["image_spec"], out["logos"])
    assert drawn and drawn[-1] == out["image_footer"]
    md = {"image_role": "post_image", "onscreen_text": drawn}
    await runs._check_post_image_text(run, md, ext="jpg", size_bytes=900_000)
    # a subset is fine (a wordmark the worker could not fit stays declared-or-not)
    await runs._check_post_image_text(run, {**md, "onscreen_text": [drawn[0], out["image_footer"]]}, ext="jpg",
                                      size_bytes=900_000)
    for extra in ("Buy now", "https://evil.example/logo.png", "a" * 64):
        with pytest.raises(mrs.MarketingRequestInvalid, match="not its image_spec"):
            await runs._check_post_image_text(run, {**md, "onscreen_text": [*drawn, extra]}, ext="jpg",
                                              size_bytes=900_000)
    with pytest.raises(mrs.MarketingRequestInvalid, match="does not draw its footer"):
        await runs._check_post_image_text(run, {**md, "onscreen_text": drawn[:-1]}, ext="jpg", size_bytes=900_000)
    # a Drop-1-shaped image (the alt title + paragraphs drawn, the lesson's layout) is refused
    lesson_shape = [out["image_post"]["title"], *out["image_post"]["paragraphs"], out["image_footer"]]
    with pytest.raises(mrs.MarketingRequestInvalid, match="not its image_spec"):
        await runs._check_post_image_text(run, {**md, "onscreen_text": lesson_shape}, ext="jpg", size_bytes=900_000)


@pytest.mark.parametrize("break_it", [
    lambda o: o["image_spec"].__setitem__("layout", "table"),                     # no such layout
    lambda o: o["image_spec"].__setitem__("layout", "grid"),                      # another layout's schema
    lambda o: o["image_spec"].__setitem__("footer", "another footer"),            # not the output's footer
    lambda o: o.__setitem__("logos", []),                                         # its logos have no entry
    lambda o: o.__setitem__("image_footer", ""),
])
@pytest.mark.asyncio
async def test_a_template_image_spec_that_does_not_validate_allows_nothing(tworld, break_it):
    sb, runs = tworld
    rid = await _template_run(sb, runs)
    out = _script(sb, rid)["output"]
    footer = out["image_footer"]
    break_it(out)
    run = _run_row(sb, rid)
    with pytest.raises(mrs.MarketingRequestInvalid):
        await runs._check_post_image_text(run, {"image_role": "post_image", "onscreen_text": [footer]},
                                          ext="jpg", size_bytes=1000)


@pytest.mark.asyncio
async def test_a_candidate_that_no_longer_rechecks_as_stored_is_refused_never_inserted(tworld, monkeypatch, caplog):
    """The build re-checks the output exactly as it will be stored (logos attached, formats frozen, JSON
    round trip). Anything those steps changed in a compared field fails it: that candidate is refused
    (`field_mismatch`) and the day falls through — create_posts would refuse it anyway, after the render."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    real = ss.freeze_post_formats

    def drifting(output, *a, **k):
        out = real(output, *a, **k)
        if output.get("authorship") == "template":
            out["hook"] = out["hook"].replace("disclosed", "reported")
        return out

    monkeypatch.setattr(ss, "freeze_post_formats", drifting)
    news = FakeNews({"ceo_buys": cands("ceo_buys", ceo_week()), "money_map": None})
    svc = _svc(runs, news)
    rid = _run(sb, MON)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["template_id"] in selection.LESSON_TEMPLATE_IDS            # fell to the lesson
    trail = _script(sb, rid)["fact_sheet"]["selection"]["trail"]
    assert trail[0] == {"series": "ceo_buys", "outcome": "all_refused", "reason": "field_mismatch"}
    assert any("failed the re-check AS STORED" in r.getMessage() for r in caplog.records)
    await _drain(svc)


@pytest.mark.asyncio
async def test_a_company_news_source_that_cannot_load_still_posts_the_lesson(tworld, monkeypatch, caplog):
    """A broken adapter import (a deploy bug) is not a reason to lose the day: every series falls through
    (`error`, logged with its stack) and the chain's lesson is selected."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    svc = _svc(runs, None)

    def broken():
        raise ImportError("cannot import name 'candidates'")

    monkeypatch.setattr(svc, "_news_source_fn", broken)
    rid = _run(sb, MON)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "generating" and state["template_id"] in selection.LESSON_TEMPLATE_IDS
    trail = _script(sb, rid)["fact_sheet"]["selection"]["trail"]
    assert [(t["series"], t["outcome"]) for t in trail] == [
        ("ceo_buys", "error"), ("insider_buys", "error"), ("money_map", "error"), ("lesson", "chosen")]
    assert any(r.exc_info and "could not be loaded" in r.getMessage() for r in caplog.records)
    await _drain(svc)


@pytest.mark.asyncio
async def test_odd_candidate_answers_are_refused_never_crash_the_build(tworld, monkeypatch):
    """A Candidates-shaped answer with no records attribute, a None records, a non-record in records, or a
    rejections mapping that is not a dict: each is read defensively and the day still lands."""
    from types import MappingProxyType

    sb, runs = tworld
    _today(monkeypatch, MON)
    news = FakeNews({
        "ceo_buys": SimpleNamespace(series="ceo_buys"),                                    # no records at all
        "insider_buys": SimpleNamespace(series="insider_buys", records=("not a record", None), skip_reason=None,
                                        rejections=None),
        "money_map": SimpleNamespace(series="money_map", records=(costco(),), skip_reason=None,
                                     rejections=MappingProxyType({"already_posted": 1})),
    })
    svc = _svc(runs, news)
    rid = _run(sb, MON)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "accepted" and state["template_id"] == "money_map"
    row = _script(sb, rid)
    assert row["fact_sheet"]["rejections"] == {"already_posted": 1}
    assert [(t["series"], t["outcome"]) for t in row["fact_sheet"]["selection"]["trail"]] == [
        ("ceo_buys", "no_candidates"), ("insider_buys", "all_refused"), ("money_map", "chosen")]
