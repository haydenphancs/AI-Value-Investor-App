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
* Batch 2 fix round (2026-10-20): on every series, a `video_v2` holder's day freezes the data card, the
  read-back names the card it replaces (`data_card_slot`: the decided fact card, never a kept hedge card),
  the worker's declared strings pass the server's check, and every VIDEO post carries the run's frozen
  `video_features` (B2-METRICS-ATTRIBUTION).
* Batch 3 (2026-10-20): on every series, with MARKETING_VIDEO_EXTRA_PLATFORMS listing Facebook / LinkedIn /
  Threads, the real build freezes the EXTRA video posts (re-checked by `news_templates.extra_video_problems`),
  the stored output still re-checks clean, the worker never reads them, and create_posts appends them after the
  worker's primary specs; with the switch empty a template day records exactly its primary posts.

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
import re
import struct
import time
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
        # owner decisions 2026-11-20 ("3 - 5 companies at once"): a Form 4 week holds three companies at least
        buy("NKE", "Nike", role, 250_000.0, 3_500.0, [end - timedelta(days=1)]),
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


def cands(series: str, *records, skip: Optional[str] = None, rejections: Optional[Dict[str, int]] = None,
          checked_window=None):
    """The adapter's `Candidates` surface (series, records, skip_reason, rejections, checked_window — C0: the Form 4
    window the insider spotlight checked, None for every other answer)."""
    return SimpleNamespace(series=series, records=tuple(records),
                           skip_reason=skip if not records else None, rejections=dict(rejections or {}),
                           checked_window=checked_window)


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

    async def candidates(self, series, *, run_date, exclude, limit, deadline, **kw):
        # `kw`: the insider spotlight's ledger inputs (the spotlight lane, 2026-11-15: `spotlight_checked_through` —
        # C0, 2026-11-16 — and `spotlight_recent`) — the build passes them for that series only, which the tests read.
        self.calls.append({"series": series, "run_date": run_date, "exclude": exclude, "limit": limit,
                           "deadline": deadline, "kw": dict(kw)})
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
    # The news series at their declared default, whatever this machine's environment says (review of 2026-10-20,
    # the HYG-1 twin): with the planned production list (it adds earnings) a Thursday's trail gained an
    # `earnings: no_candidates` step first. The tests that need another list set it themselves.
    from app.config import Settings
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", Settings.model_fields["MARKETING_NEWS_SERIES"].default)
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_POSTS", True)
    monkeypatch.setattr(mrs.settings, "MARKETING_X_IMAGES", False)
    monkeypatch.setattr(mrs.settings, "MARKETING_X_ALLOW_URLS", False)
    # news-v22: the first-line prefixes at their declared default (off) — the tests that need them on set it
    monkeypatch.setattr(mrs.settings, "MARKETING_FIRST_LINE_PREFIX", False)
    # Batch 3: the three new switches at their declared defaults (no extra video, the four posting days, no
    # trial Reels), whatever this machine's environment says.
    monkeypatch.setattr(mrs.settings, "MARKETING_VIDEO_EXTRA_PLATFORMS", "")
    monkeypatch.setattr(mrs.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat")
    monkeypatch.setattr(mrs.settings, "MARKETING_INSTAGRAM_TRIAL_MODE", "off")
    # The spotlight lane (migration 210) at its declared defaults — closed — whatever this machine's environment or
    # backend/.env says: `selection.news_recent_limit()` grows by the spotlight days while the lane is open, which
    # moved the 420-row cadence golden to 600 (lane review, 2026-11-15). The tests that need it open set it.
    for name in ("MARKETING_SPOTLIGHT_ENABLED", "MARKETING_SPOTLIGHT_WEEKDAYS", "MARKETING_SPOTLIGHT_HOUR_ET"):
        monkeypatch.setattr(mrs.settings, name, Settings.model_fields[name].default)
    # Per-post art (workstream C) at its declared default — off — whatever this machine's environment says: the tests
    # that make art turn it on themselves (`art_world`).
    monkeypatch.setattr(mrs.settings, "MARKETING_ART_ENABLED", Settings.model_fields["MARKETING_ART_ENABLED"].default)
    monkeypatch.setattr(mrs.settings, "SUPABASE_URL", _SB_PUBLIC)
    monkeypatch.setattr(ss.smart_link, "store_state", lambda: "live")
    monkeypatch.setattr(ss, "_FINISH_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(ss, "_REFRESH_BACKOFF_SECONDS", 0.0)
    return sb, runs


def _today(monkeypatch, day: date) -> None:
    monkeypatch.setattr(ss, "_today_et", lambda: day)
    if day.weekday() == selection.THEME_WEEKDAY:
        # news-v23 phase 3: the weekly theme post runs on Wednesday only — a posting day of the owner's production
        # calendar (documents/OWNER_TASKS.md), which the declared default (mon,tue,thu,sat) rests
        monkeypatch.setattr(mrs.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,wed,thu,sat")


#: What the drop-2 worker declares on its claim (marketing/main.py BackendClient.WORKER_CAPABILITIES).
DROP2_CAPS = ["layouts_2b", "news_templates", "post_image"]
#: What a drop-2a worker image declared: it draws templates, but refuses a `pair` / `grid` image.
DROP2A_CAPS = ["news_templates", "post_image"]


def _v23_caps(series: str, base: Optional[List[str]] = None) -> List[str]:
    """`base` (DROP2_CAPS) + look_v23 for a series whose picture needs the news-v23 rows (the earnings table, a stake's
    basis label) — an older holder's chain never holds it (`script_service._without_look_series`)."""
    caps = list(DROP2_CAPS if base is None else base)
    if series == ss.THEME_CONTEXT_SERIES:
        # news-v23 phase 3: the theme post's angles, chips and icon — and its list cover, which (like every news-v23
        # list cover) also needs look_v23: the worker that declares theme_angles declares both
        return [*caps, "look_v23", "theme_angles"]
    return [*caps, "look_v23"] if series in ss.LOOK_V23_SERIES else caps


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
    # news-v23 (restyle 2026-11-19): the Form 4 family's short footer
    assert out["image_footer"].startswith("Not investment advice · Source: SEC Form 4 · filed ")
    assert "AI" not in out["image_footer"] and "Caydex" not in out["image_footer"]
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
    # news-v23: this drop-2 holder did not declare look_v23 — its picture comes without the ticker chips, and (2026-11-20)
    # the roundup's cover without its list (the plain card: the kicker, the lead row's logo, the roundup's headline)
    assert "list" in out["opening_card"]
    assert script["opening_card"] == ss._without_list_cover(out["opening_card"], rid) == {
        k: v for k, v in out["opening_card"].items() if k != "list"}
    assert script["image_spec"] == ss._without_look_chips(out["image_spec"], rid)
    assert script["look"] == "news-v23"
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


# ══ RR2-MM-TEMPLATE-WALL (the round-2 review of 2026-10-20): a template refusal is reported back to the source ═══
#
# Nothing fed a template refusal back to the adapter: a Money Map record the template refused was rebuilt and offered
# first every day, and three of them at the head of the pool ended the series every day. `_build_template_day` now
# reports each refused candidate (`_report_template_refusal` → the adapter's `remember_template_refusal`).


class ReportingNews(FakeNews):
    """A source with the adapter's `remember_template_refusal` hook: each call is recorded as (ledger key, codes,
    run date); ``fail`` makes the hook raise after recording."""

    def __init__(self, answers: Dict[str, Any], *, fail: Optional[BaseException] = None, **kw) -> None:
        super().__init__(answers, **kw)
        self.reported: List[tuple] = []
        self.fail = fail

    def remember_template_refusal(self, record, codes=(), *, run_date):
        self.reported.append((R.ledger_key(record), tuple(codes), run_date))
        if self.fail is not None:
            raise self.fail
        return True


def loss_costco() -> R.MoneyMap:
    """Valid as a record, refused by its template: a net loss larger than revenue (`implausible_figures`)."""
    return costco(company=co("LOSS", "Lossmaker"), net_income_usd=-300_000_000_000.0)


def domain_costco() -> R.MoneyMap:
    """Valid as a record, refused by its template: a segment named like a web address (`slot_rejected`)."""
    return costco(company=co("BKNG", "Booking"), segments=(
        R.Segment("Booking.com", 200_000_000_000.0), R.Segment("Canada", 38_500_000_000.0),
        R.Segment("Other International", 36_700_000_000.0)))


@pytest.mark.asyncio
async def test_a_refused_candidate_is_reported_back_to_its_source_and_the_next_one_posts(tworld, monkeypatch):
    """The refused record — and only it — is reported with its own refusal code and the run date; the next record
    posts as before. Mutation-checked (2026-10-20): without the `_report_template_refusal` call nothing is
    reported."""
    sb, runs = tworld
    _today(monkeypatch, THU)
    news = ReportingNews({"money_map": cands("money_map", stale_costco(), costco())})
    svc = _svc(runs, news)
    rid = _run(sb, THU)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "accepted" and state["source_ref"] == "news:money_map:COST:2025"
    assert news.reported == [("news:money_map:COST:2022", ("stale_source",), THU)]


@pytest.mark.asyncio
async def test_every_refused_candidate_is_reported_and_the_day_falls_to_the_lesson(tworld, monkeypatch):
    """Three records the template refuses, each for its own reason: each is reported once with that reason (in the
    order offered), the series falls back `all_refused` and the day goes to the lesson."""
    sb, runs = tworld
    _today(monkeypatch, THU)
    news = ReportingNews({"money_map": cands("money_map", stale_costco(), loss_costco(), domain_costco())})
    svc = _svc(runs, news)
    rid = _run(sb, THU)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["template_id"] in selection.LESSON_TEMPLATE_IDS
    assert news.reported == [("news:money_map:COST:2022", ("stale_source",), THU),
                             ("news:money_map:LOSS:2025", ("implausible_figures",), THU),
                             ("news:money_map:BKNG:2025", ("slot_rejected",), THU)]
    trail = _script(sb, rid)["fact_sheet"]["selection"]["trail"]
    assert trail[0] == {"series": "money_map", "outcome": "all_refused", "reason": "implausible_figures"}
    assert trail[-1] == {"series": "lesson", "outcome": "chosen"}
    await _drain(svc)


@pytest.mark.asyncio
async def test_a_report_hook_that_raises_never_costs_the_day_its_next_candidate(tworld, monkeypatch, caplog):
    """The report is best effort: a hook that raises is logged at WARNING (the series, the symbol, the code) and the
    next candidate still posts. Mutation-checked (2026-10-20): without the guard the build crashes."""
    sb, runs = tworld
    _today(monkeypatch, THU)
    caplog.set_level(logging.WARNING, logger=ss.__name__)
    news = ReportingNews({"money_map": cands("money_map", stale_costco(), costco())}, fail=RuntimeError("memo down"))
    svc = _svc(runs, news)
    rid = _run(sb, THU)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "accepted" and state["source_ref"] == "news:money_map:COST:2025"
    assert len(news.reported) == 1
    assert ("marketing series money_map: the template refusal of COST (stale_source) could not be reported to the "
            "company-news source") in caplog.text and "RuntimeError: memo down" in caplog.text


@pytest.mark.asyncio
async def test_a_stake_refusal_the_hook_cannot_record_is_logged_by_its_stake_id(tworld, monkeypatch, caplog):
    """RR3-STAKES-TEMPLATE-WALL: stakes are reported back too. A stake the template refuses ("%" in its source title)
    is reported with its code and the run date; a hook that raises is logged at WARNING naming the STAKE ID (a stake
    has no company symbol), and the next stake still posts. Mutation-checked (2026-10-20, in memory): without the
    stake-id fallback the line says "a record"."""
    import test_marketing_news_templates as NT

    sb, runs = tworld
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "company_stakes")
    _today(monkeypatch, NT.STAKES_RUN)
    caplog.set_level(logging.WARNING, logger=ss.__name__)
    # news-v23: a post whose only stake the template refuses is refused whole, with the stake's own code
    refused = NT.one(NT.stake(stake_id="0b8f3c2e-1d4a-4e6b-8c9d-2a7e5f1b3c04", source_title="Intel 8-K (a 9% stake)"))
    news = ReportingNews({"company_stakes": cands("company_stakes", refused, NT.nvidia_stakes())},
                         fail=RuntimeError("memo down"))
    svc = _svc(runs, news)
    rid = _run(sb, NT.STAKES_RUN, caps=_v23_caps("company_stakes"))
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["source_ref"] == R.ledger_key(NT.nvidia_stakes())
    assert news.reported == [(R.ledger_key(refused), ("slot_rejected",), NT.STAKES_RUN)]
    assert ("marketing series company_stakes: the template refusal of NVDA's stakes "
            "0b8f3c2e-1d4a-4e6b-8c9d-2a7e5f1b3c04 (slot_rejected) could not be reported") in caplog.text


class _AdapterNews(FakeNews):
    """The REAL adapter's Money Map walk over the adapter suite's fakes and its REAL `remember_template_refusal`; any
    other series answers "nothing qualified". Records each walk's breakdown reads."""

    def __init__(self, fmp, world) -> None:
        super().__init__({})
        from app.services.marketing import company_news_adapter as A

        self.A, self.fmp, self.world = A, fmp, world
        self.MarketingNewsUnavailable = A.MarketingNewsUnavailable
        self.reads: List[List[str]] = []

    async def candidates(self, series, *, run_date, exclude, limit, deadline, **_kw):
        if series != "money_map":
            return await super().candidates(series, run_date=run_date, exclude=exclude, limit=limit,
                                            deadline=deadline)
        import test_marketing_company_news_adapter as TA

        self.world["revenue"].calls.clear()
        got = await self.A.candidates(series, run_date=run_date, exclude=exclude, limit=limit, deadline=deadline,
                                      deps=self.A.NewsDeps(fmp=self.fmp, actions=TA.SplitActions(), **self.world))
        self.reads.append(list(self.world["revenue"].calls))
        return got

    def remember_template_refusal(self, record, codes=(), *, run_date):
        return self.A.remember_template_refusal(record, codes, run_date=run_date)


@pytest.mark.asyncio
async def test_the_real_walk_stops_offering_first_a_company_its_template_refused(tworld, monkeypatch):
    """End to end through the REAL `_build_template_day` and the REAL adapter walk: three never-posted companies whose
    largest segment is named like a web address (`slot_rejected`, a refusal only the template makes) head the pool,
    a publishable one behind them. The first Thursday the three records are refused and the day falls to the lesson;
    the build reports them, so the next Thursday's walk reads the publishable company FIRST, answers the three from
    the memo, and the day posts it. Mutation-checked (2026-10-20): without the report the second Thursday reads the
    same three first and falls to the lesson again."""
    import test_marketing_company_news_adapter as TA
    from app.services.marketing import company_news_adapter as A

    sb, runs = tworld
    A.clear_memo()
    try:
        monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", False)
        fmp, world, set_day = TA._template_wall_pool(monkeypatch, ("WA", "WB", "WC"), ("PA",), (),
                                                     segment="Booking.com")
        news = _AdapterNews(fmp, world)
        svc = _svc(runs, news)
        first, second = THU, THU + timedelta(days=7)
        _today(monkeypatch, first)
        set_day(first)
        rid = _run(sb, first)
        state = await svc.kick(rid, claim=_claim(sb, rid))
        assert state["template_id"] in selection.LESSON_TEMPLATE_IDS
        assert _script(sb, rid)["fact_sheet"]["selection"]["trail"][0] == {
            "series": "money_map", "outcome": "all_refused", "reason": "slot_rejected"}
        await _drain(svc)
        _today(monkeypatch, second)
        set_day(second)
        rid2 = _run(sb, second)
        state = await svc.kick(rid2, claim=_claim(sb, rid2))
        assert state["status"] == "accepted" and state["source_ref"] == "news:money_map:PA:2025"
        assert news.reads == [["WA", "WB", "WC"], ["PA", "WA", "WB"]]
        assert _script(sb, rid2)["fact_sheet"]["rejections"] == {"template_refused": 2}
    finally:
        A.clear_memo()


class _AdapterStakesNews(FakeNews):
    """The REAL adapter's company_stakes walk over the adapter suite's stakes fakes (`stakes_world`) and its REAL
    `remember_template_refusal`; any other series answers "nothing qualified". Records the investees each walk
    offered."""

    def __init__(self, rows) -> None:
        super().__init__({})
        from app.services.marketing import company_news_adapter as A

        self.A, self.rows = A, rows
        self.MarketingNewsUnavailable = A.MarketingNewsUnavailable
        self.offered: List[List[str]] = []

    async def candidates(self, series, *, run_date, exclude, limit, deadline, **_kw):
        if series != "company_stakes":
            return await super().candidates(series, run_date=run_date, exclude=exclude, limit=limit,
                                            deadline=deadline)
        import test_marketing_company_news_adapter as TA

        club, sb, fmp = TA.stakes_world(self.rows)
        got = await self.A.candidates(series, run_date=run_date, exclude=exclude, limit=limit, deadline=deadline,
                                      deps=self.A.NewsDeps(fmp=fmp, club=club, sb=sb, actions=TA.SplitActions()))
        self.offered.append(TA._posts(got))
        return got

    def remember_template_refusal(self, record, codes=(), *, run_date):
        return self.A.remember_template_refusal(record, codes, run_date=run_date)


@pytest.mark.asyncio
async def test_the_real_stakes_walk_drops_a_refused_stake_and_never_ranks_its_investor_first_again(tworld, monkeypatch):
    """The round-3 review of 2026-10-20 (RR3-STAKES-TEMPLATE-WALL), news-v23 (one investor's stakes a post), end to end
    through the REAL `_build_template_day` and the REAL adapter walk: NVIDIA holds Intel (publishable) and three stakes
    only the template refuses (a "%" in their shared source title — a slot rule the read does not apply); Amazon holds
    Rivian. The first Tuesday NVIDIA leads (four public stakes — a post's most since the review of 2026-11-20): its
    post DROPS the three (`drop_refused_rows`), each reported back (`_compose_candidate`), and posts Intel alone. The
    next Tuesday NVIDIA's three are remembered, so Amazon — one fresh stake against none — is offered FIRST and
    posted. Mutation-checked (2026-11-20, in memory): without the per-stake report in `_compose_candidate`, the second
    Tuesday offers NVIDIA's stakes first again."""
    import test_marketing_company_news_adapter as TA
    from app.services.marketing import company_news_adapter as A

    sb, runs = tworld
    A.clear_memo()
    try:
        monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
        monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "company_stakes")
        five = ["Nscale", "Ayar Labs", "Lambda"]
        rows = [TA.stake("nvidia", n, as_of=f"2026-11-{10 + i:02d}", source_title="NVIDIA 10-Q (a 9% stake)")
                for i, n in enumerate(five)]
        rows.append(TA.stake("nvidia", "Intel", investee_us_symbol="INTC", disclosed_value_usd=5e9,
                             as_of="2025-12-26", source_title="Intel 8-K (Dec 29, 2025)"))
        rows.append(TA.stake("amazon", "Rivian Automotive", investee_us_symbol="RIVN", as_of="2026-04-23",
                             source_title="Amazon 10-Q (quarter ended Jun 30, 2026)"))
        news = _AdapterStakesNews(rows)
        svc = _svc(runs, news)
        first, second = date(2026, 12, 29), date(2027, 1, 5)
        assert selection.plan_for(first).chain[0] == selection.plan_for(second).chain[0] == "company_stakes"
        _today(monkeypatch, first)
        rid = _run(sb, first, caps=_v23_caps("company_stakes"))
        state = await svc.kick(rid, claim=_claim(sb, rid))
        await _drain(svc)
        assert state["status"] == "accepted" and state["source_ref"] == f"news:company_stakes:{rows[3]['id']}"
        _today(monkeypatch, second)
        rid2 = _run(sb, second, caps=_v23_caps("company_stakes"))
        state = await svc.kick(rid2, claim=_claim(sb, rid2))
        await _drain(svc)
        assert state["status"] == "accepted" and state["source_ref"] == f"news:company_stakes:{rows[4]['id']}"
        assert news.offered == [[["Intel"] + five, ["Rivian Automotive"]], [["Rivian Automotive"], five]]
    finally:
        A.clear_memo()


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

        async def recent_source_refs(self, before, limit, **kw):
            if Blip.fail:
                Blip.fail -= 1
                raise mrs.MarketingRunError("recent_source_refs failed: 520")
            return await super().recent_source_refs(before, limit, **kw)

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


def test_the_template_timing_constants_fit_the_workers_own(monkeypatch):
    from app.config import Settings

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
    # Batch 3 (D17): the call-time read the build and the lesson make equals that constant at the declared default
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS",
                        Settings.model_fields["MARKETING_POST_WEEKDAYS"].default)
    assert ss.NEWS_RECENT_LIMIT == 240 == selection.news_recent_limit()
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
    # news-v23: company stakes draw a `rows` list (a look_v23 series, `ss.LOOK_V23_SERIES`), no longer the `pair`
    assert ss.LAYOUTS_2B_SERIES == {"theme_explainer"}
    assert ss.LAYOUTS_2B_SERIES <= selection.SHIPPED_SERIES


@pytest.mark.parametrize("series", ["theme_explainer"])
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
    # a drop-2a holder declared no look_v23 either: the news-v23 list series leave its chain too (`_without_look_series`)
    kept = [s for s in full if s not in ss.LAYOUTS_2B_SERIES and s not in ss.LOOK_V23_SERIES]
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
    # news-v23 phase 3: the theme runs on Wednesday (its only day); company stakes are a news-v23 `rows` list now
    rec, day, _klass, _layout = _2b_case("theme_explainer")
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "company_stakes,theme_explainer")
    _today(monkeypatch, day)
    news = FakeNews({"theme_explainer": cands("theme_explainer", rec)})
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
    for series in ("theme_explainer",):               # news-v23: company stakes draw `rows` (a 2a worker draws it)
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
    assert keys == ["LOW", "SBUX", "NKE"]                       # owner decisions 2026-11-20: three companies at least
    news = FakeNews({}, logos={"LOW": (b"GIF89a", "image/gif"), "SBUX": None, "NKE": None})
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

# Every SHIPPED series, in registry order (news-v22: the spotlight pair included — a main-lane chain never names it).
EVERY_SERIES = ",".join(s.id for s in selection.SERIES if s.id in selection.SHIPPED_SERIES)


def _2b_case(series: str):
    import test_marketing_news_templates as NT

    # news-v23 (2026-11-19): the Congress Count is ONE list of the month's 2-3 companies (`rows`, was `spotlight`)
    return {"congress_count": (NT.congress(), NT.CONGRESS_RUN, "C", "rows"),
            # news-v23 (owner 2026-11-19): one investor's stakes in public companies — a `rows` list (was `pair`)
            "company_stakes": (NT.nvidia_stakes(), NT.STAKES_RUN, "F", "rows"),
            "earnings": (NT.earnings(), NT.EARNINGS_RUN, "F", "rows"),
            # news-v23 phase 3: the weekly theme post, Wednesday's lead, under its new key (angle 1)
            "theme_explainer": (NT.ai_chips_weekly(), NT.THEME_WED, "F", "grid")}[series]


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
    rid = _run(sb, day, caps=_v23_caps(series))
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
    # the alt text is never drawn (2026-11-20: a Form 4 roundup's alt TITLE is its picture's title, which its list cover
    # now draws as its headline — so the alt PARAGRAPH is the never-drawn alt string here)
    assert out["image_post"]["title"] == out["opening_card"]["headline"]
    for extra in ("Buy now", f"{_SB_PUBLIC}/storage/v1/object/public/marketing-media/logos/" + "a" * 32 + ".png",
                  out["image_post"]["paragraphs"][0]):
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



# ══ Batch 2 fix round (2026-10-20): every series' data card, from the build to the posts ═════════════════


def _every_case(series: str):
    """(record, run day) of each series — the templates' own samples, on a day whose chain reaches it."""
    import test_marketing_news_templates as NT

    return {"ceo_buys": (NT.ceo_roundup(), NT.MON), "insider_buys": (NT.insider_mixed(), NT.MON),
            "thirteen_f": (NT.berkshire(), NT.TUE), "money_map": (NT.costco(), NT.THU),
            "congress_count": (NT.congress(), NT.CONGRESS_RUN), "company_stakes": (NT.nvidia_stakes(), NT.STAKES_RUN),
            "earnings": (NT.earnings(), NT.EARNINGS_RUN),
            "theme_explainer": (NT.ai_chips_weekly(), NT.THEME_WED)}[series]           # news-v23 phase 3: Wednesdays


#: The card each series' data card replaces (CARDS-B2-1): the decided fact card, by its words. news-v21: an earnings
#: post has no "Revenue" card (the revenue estimate's basis is unknown), so its date card is the slot. news-v23
#: (2026-11-19): the 13F roundup, the Congress Count and earnings have none — each video's cover IS its list
#: (`template_onscreen.NO_DATA_CARD_SERIES`, pinned below by `test_a_list_series_freezes_no_data_card_its_cover_is_the_list`).
#: news-v23: company stakes have none either (one investor's stakes: the cover is their list).
_DECIDED_SLOT = {"ceo_buys": (1, "The purchases"), "insider_buys": (1, "The purchase"),
                 "money_map": (1, "Revenue mix")}
#: The series whose video cover is the list (news-v23): no data card, whatever the switch asks for. news-v23 phase 3:
#: the weekly theme post too (its cover lists the picture's first companies; its picture is the `grid`).
_LIST_COVER_KICKERS = {"thirteen_f": "13F FILING", "congress_count": "CONGRESS · DISCLOSED IN NOVEMBER",
                       "earnings": "EARNINGS VS ESTIMATES", "company_stakes": "COMPANY STAKES",
                       "theme_explainer": "INSIDE A THEME"}
_LIST_PICTURE_LAYOUT = {"theme_explainer": "grid"}


@pytest.mark.parametrize("series", sorted(_DECIDED_SLOT))
@pytest.mark.asyncio
async def test_every_series_freezes_its_data_card_names_its_slot_and_stamps_its_video_posts(tworld, monkeypatch,
                                                                                        series):
    sb, runs = tworld
    rec, day = _every_case(series)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    monkeypatch.setattr(ss.settings, "MARKETING_VIDEO_FEATURES", "data_card,push_in")
    _today(monkeypatch, day)
    svc = _svc(runs, FakeNews({series: cands(series, rec)}))
    rid = _run(sb, day, caps=[*DROP2_CAPS, "video_v2"])
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == series
    out = _script(sb, rid)["output"]
    assert out["video_features"] == ["data_card", "push_in"] and "data_card_slot" not in out    # never stored
    script = state["script"]
    assert script["video_features"] == ["data_card", "push_in"]
    slot = script["data_card_slot"]
    assert (slot, script["cards"][slot]["title"]) == _DECIDED_SLOT[series]
    kept = {news_templates.LEXICON[i] for i in onscreen.DATA_CARD_KEEPS[series]}
    assert script["cards"][slot]["title"] not in kept
    # what the worker declares — the opening card, every text card but the slot, the picture, the disclaimer,
    # the end card — is exactly inside the server's allow-list
    texts = [c[k] for i, c in enumerate(out["cards"]) if i != slot for k in ("title", "body")]
    drawn = list(dict.fromkeys([*onscreen.opening_strings(out["opening_card"], out["logos"]), *texts,
                                *onscreen.image_strings(out["image_spec"], out["logos"]), out["disclaimer_card"],
                                script["end_card_cta"]]))
    await runs._check_onscreen_text(_run_row(sb, rid), {"onscreen_text": drawn, "voice_asset_id": _voice(sb, rid)})
    # every VIDEO post says which features its video was asked for; the picture and text posts say nothing
    video, card = _ready(sb, rid, "video"), _ready(sb, rid, "card", image_role="post_image")
    _run_row(sb, rid)["metadata"].update({"video_asset_id": video, "image_asset_id": card})
    specs = [{"platform": p, "format": f, **({"asset_ids": [video if f == "video" else card]} if f != "text" else {})}
             for p, f in sorted(out["post_formats"].items())]
    posts = await runs.create_posts(rid, specs, claim=_claim(sb, rid))
    assert {p["format"] for p in posts} >= {"video", "text"}
    for p in posts:
        assert p["metadata"].get("video_features") == (["data_card", "push_in"] if p["format"] == "video" else None)


@pytest.mark.parametrize("series", sorted(_LIST_COVER_KICKERS))
@pytest.mark.asyncio
async def test_a_list_series_freezes_no_data_card_its_cover_is_the_list(tworld, monkeypatch, series):
    """news-v23 (owner 2026-11-19: the ONE 13F list; the earnings table and the Congress list of the same day): the
    video opens on the list itself, so the switch asking for the data card freezes only push-in, the read-back names
    no slot, and every video post says so."""
    sb, runs = tworld
    rec, day = _every_case(series)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    monkeypatch.setattr(ss.settings, "MARKETING_VIDEO_FEATURES", "data_card,push_in")
    _today(monkeypatch, day)
    svc = _svc(runs, FakeNews({series: cands(series, rec)}))
    rid = _run(sb, day, caps=_v23_caps(series, [*DROP2_CAPS, "video_v2"]))
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == series
    out = _script(sb, rid)["output"]
    assert out["video_features"] == ["push_in"] and out["image_spec"]["layout"] == _LIST_PICTURE_LAYOUT.get(series,
                                                                                                          "rows")
    assert out["opening_card"]["list"]["rows"] and out["opening_card"]["kicker"] == _LIST_COVER_KICKERS[series]
    script = state["script"]
    assert script["video_features"] == ["push_in"] and script.get("data_card_slot") is None
    assert series in onscreen.NO_DATA_CARD_SERIES


_WORKER_FONT = str(_BACKEND / "marketing" / "assets" / "fonts" / "Inter-Bold.ttf")
#: The series whose sample picture fits the data card legibly (the worker's 22 px floor, DATA_CARD_MIN_PX —
#: measured the same with the basic and the raqm layout engine). news-v23 (restyle 2026-11-19): every text style's
#: floor is now 22 px or more, so EVERY series' sample fits — Costco's Money Map bars and the AI-chips grid, which drew
#: a 21 px cell / tile line in the old look, included. The degraded path (the slot's TEXT card kept, WARNING, never a
#: data card in another place) is pinned with a raised floor in tests/test_marketing_news_layouts.py; the branch
#: below still checks it for any series a future style leaves out of this set.
_PICTURE_FITS = frozenset(_DECIDED_SLOT)


@pytest.mark.parametrize("series", sorted(_DECIDED_SLOT))
@pytest.mark.asyncio
async def test_the_real_worker_draws_every_series_data_card_at_the_read_backs_slot(tworld, monkeypatch, caplog,
                                                                                 series):
    """The slot contract end to end on every series (the real-worker e2e days reach five): the web's read-back
    as it crosses the wire (WorkerScript → JSON) drawn by the REAL worker cards — the data card exactly at the
    slot the read-back names (specs[slot + 1]) or, where the picture cannot fit legibly, nowhere (the text
    card stays, said at WARNING); every other content card a text card in its own place (the series' kept
    hedge cards among them); and what the worker declares inside the real server's allow-list."""
    pytest.importorskip("PIL")
    from marketing import cards

    sb, runs = tworld
    rec, day = _every_case(series)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    monkeypatch.setattr(ss.settings, "MARKETING_VIDEO_FEATURES", "data_card,push_in")
    _today(monkeypatch, day)
    svc = _svc(runs, FakeNews({series: cands(series, rec)}))
    rid = _run(sb, day, caps=[*DROP2_CAPS, "video_v2"])
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == series
    sent = json.loads(WorkerScript(**state["script"]).model_dump_json())
    slot = sent["data_card_slot"]
    assert (slot, sent["cards"][slot]["title"]) == _DECIDED_SLOT[series]
    with caplog.at_level(logging.WARNING, logger="marketing.cards"):
        specs = cards.cards_for_script(sent, font_path=_WORKER_FONT, layout_engine="basic")
    unfit = [r for r in caplog.records if r.name == "marketing.cards" and "does not fit legibly" in r.getMessage()]
    kinds = ["opening", *["text"] * len(sent["cards"]), "disclaimer"]
    if series in _PICTURE_FITS:
        kinds[slot + 1] = "data"
        assert unfit == []
    else:
        assert len(unfit) == 1 and repr(sent["cards"][slot]["title"]) in unfit[0].getMessage()
    assert [s.kind for s in specs] == kinds
    assert cards.applied_data_card_slot(specs) == (slot if series in _PICTURE_FITS else None)
    assert [s.title for s in specs if s.kind == "text"] == [c["title"] for i, c in enumerate(sent["cards"])
                                                             if i != slot or series not in _PICTURE_FITS]
    kept = {news_templates.LEXICON[i] for i in onscreen.DATA_CARD_KEEPS[series]}
    assert sent["cards"][slot]["title"] not in kept
    drawn = list(dict.fromkeys(t for s in specs for t in cards.onscreen_strings(s)))
    assert {c["title"] for c in sent["cards"] if c["title"] in kept} <= set(drawn)
    await runs._check_onscreen_text(_run_row(sb, rid), {"onscreen_text": drawn, "voice_asset_id": _voice(sb, rid)})


# ══ Batch 3 (2026-10-20, contract): the ledger read follows the posting-day switch ═══════════════════════


@pytest.mark.asyncio
async def test_batch3_a_seven_day_cadence_reads_420_rows(tworld, monkeypatch):
    """D17: the day's ledger read is RECENT_WINDOW posting days of history at the cadence in force NOW
    (`selection.news_recent_limit()`, read per call) — 420 rows at seven days a week — on both of its readers:
    the template build (its exclude set, and the lesson it may fall to) and the lesson-only `_select`. With
    fewer rows a seven-day week would let the lesson rotation and the news exclude set see less history than
    the default four days do."""
    sb, runs = tworld
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,wed,thu,fri,sat,sun")
    reads: List[tuple] = []
    real = runs.recent_source_refs

    async def spy(before, limit, **kw):
        reads.append((before, limit, *kw.values()))
        return await real(before, limit, **kw)

    monkeypatch.setattr(runs, "recent_source_refs", spy)
    # a Monday news build (class C on, `tworld`) — its read names the run's lane (migration 210: the other lane's
    # same-day refs come first); the lesson-only `_select` read below names none (a lesson never sees news refs)
    _today(monkeypatch, MON)
    news = FakeNews({"ceo_buys": cands("ceo_buys", ceo_week())})
    rid = _run(sb, MON)
    state = await _svc(runs, news).kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "accepted" and state["template_id"] == "ceo_buys"
    assert reads == [(MON, 420, "main")]
    # a lesson-only day (Saturday's plan is the lesson alone), through `_select`
    reads.clear()
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    row = await svc._select({"id": str(uuid.uuid4()), "run_date": SAT.isoformat()})
    assert row["status"] == "selected" and reads == [(SAT, 420)]
    # back at the declared default, the same reads are today's 240 rows
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat")
    reads.clear()
    await svc._select({"id": str(uuid.uuid4()), "run_date": (SAT + timedelta(days=7)).isoformat()})
    assert reads == [(SAT + timedelta(days=7), 240)] and ss.NEWS_RECENT_LIMIT == 240


# ══ Batch 3 (2026-10-20): a template day with EXTRA video posts, from the build to create_posts ══════════════


@pytest.mark.parametrize("series", sorted({*_DECIDED_SLOT, *_LIST_COVER_KICKERS}))
@pytest.mark.asyncio
async def test_batch3_a_template_day_with_extras_from_build_to_create_posts(tworld, monkeypatch, series):
    """MARKETING_VIDEO_EXTRA_PLATFORMS=facebook,linkedin,threads at write time: the REAL build freezes the extras
    (each caption the platform's accepted caption under the template's narrated-video disclaimer, re-checked by
    `news_templates.extra_video_problems` against the record's fact sheet), the stored output still re-checks
    clean (`revalidate`, as create_posts runs it) with the keys present, the worker's read-back never carries them,
    and create_posts — given the worker's PRIMARY specs only — appends the three video posts on the run's video:
    pending review, made with AI (the narration), TEMPLATE_NOTE_VIDEO + NON_AFFILIATION, never an AI-written
    line."""
    sb, runs = tworld
    rec, day = _every_case(series)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    monkeypatch.setattr(ss.settings, "MARKETING_VIDEO_EXTRA_PLATFORMS", "facebook,linkedin,threads")
    # B3-LIVE-1: a Form 4 roundup's caption speaks of the picture's rows — its extras ride only a video that
    # draws the picture, so its day freezes the data card (a `video_v2` holder) and the video says it drew it
    roundup = series in _PICTURE_BOUND_SAMPLES
    if roundup:
        monkeypatch.setattr(ss.settings, "MARKETING_VIDEO_FEATURES", "data_card,push_in")
    _today(monkeypatch, day)
    svc = _svc(runs, FakeNews({series: cands(series, rec)}))
    rid = _run(sb, day, caps=[*DROP2_CAPS, "video_v2"] if roundup else _v23_caps(series))
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == series
    row = _script(sb, rid)
    out = row["output"]
    # news-v23 phase 3: a theme's Threads caption (500 bytes) cannot list the companies its video cover leaves out
    # ("+10 more"), so its extra video is skipped (`cover_caption`, WARNING) — never a video post breaking the promise
    want = {"facebook": "video", "linkedin": "video"} if series == "theme_explainer" else {
        "facebook": "video", "linkedin": "video", "threads": "video"}
    assert out["extra_post_formats"] == want
    assert news_templates.revalidate(out, fact_sheet=row["fact_sheet"], run_date=day) == []
    for p, post in out["video_posts"].items():
        assert post == {"title": None, "caption": post_copy.extra_video_caption(p, out["posts"][p]["caption"], day,
                                                                                 "template")}
        assert news_templates.extra_video_problems(out, fact_sheet=row["fact_sheet"], run_date=day, platform=p,
                                                   caption=post["caption"], video_draws_picture=True) == []
        plain = news_templates.extra_video_problems(out, fact_sheet=row["fact_sheet"], run_date=day, platform=p,
                                                    caption=post["caption"])
        assert [v["code"] for v in plain] == (["picture_caption"] if roundup else [])
    script = state["script"]
    assert not {"extra_post_formats", "video_posts"} & set(script)
    assert not {"extra_post_formats", "video_posts"} & set(json.loads(WorkerScript(**script).model_dump_json()))
    video = _ready(sb, rid, "video", metadata={"data_card_slot": script["data_card_slot"]} if roundup else None)
    card = _ready(sb, rid, "card", image_role="post_image")
    _run_row(sb, rid)["metadata"].update({"video_asset_id": video, "image_asset_id": card})
    specs = [{"platform": p, "format": f, **({"asset_ids": [video if f == "video" else card]} if f != "text" else {})}
             for p, f in sorted(out["post_formats"].items())]
    posts = await runs.create_posts(rid, specs, claim=_claim(sb, rid))
    assert [(p["platform"], p["format"]) for p in posts[:len(specs)]] == [(s["platform"], s["format"]) for s in specs]
    extra = posts[len(specs):]
    assert [(p["platform"], p["format"]) for p in extra] == [(p, "video") for p in sorted(want)]
    for p in extra:
        assert p["status"] == "pending_review" and p["asset_ids"] == [video] and p["title"] is None
        assert p["caption"] == out["video_posts"][p["platform"]]["caption"]
        note = post_copy.TEMPLATE_NOTE_VIDEO if p["platform"] != "threads" else "Narration voiced with AI."
        assert note in p["caption"] and p["caption"].count(post_copy.NON_AFFILIATION) == 1
        assert not any(line.casefold() in p["caption"].casefold() for line in post_copy.AI_DISCLOSURES)
        md = p["metadata"]
        assert md["made_with_ai"] is True
        assert (md["content_class"], md["series"], md["authorship"]) == (R.SERIES_CLASS[series], series, "template")
        # the picture / text post of the same platform keeps its own caption and its template flag
        primary = next(q for q in posts[:len(specs)] if q["platform"] == p["platform"])
        assert primary["caption"] == out["posts"][p["platform"]]["caption"] and primary["metadata"]["made_with_ai"] is False
        assert p["caption"].split("\n", 1)[0] == primary["caption"].split("\n", 1)[0]
    # a retry adopts every row
    again = await runs.create_posts(rid, specs, claim=_claim(sb, rid))
    assert [p["id"] for p in again] == [p["id"] for p in posts]


#: The `_every_case` samples whose Facebook / LinkedIn / Threads caption speaks of the picture's rows (B3-LIVE-1):
#: the two Form 4 roundups.
_PICTURE_BOUND_SAMPLES = frozenset({"ceo_buys", "insider_buys"})


@pytest.mark.parametrize("series", sorted(_PICTURE_BOUND_SAMPLES))
@pytest.mark.parametrize("how", ["no_data_card_frozen", "the_worker_kept_the_text_card"])
@pytest.mark.asyncio
async def test_batch3_a_roundups_extras_never_ride_a_video_without_the_picture(tworld, monkeypatch, caplog, series,
                                                                                how):
    """B3-LIVE-1 / B3-FR-1 (2026-10-20): the Form 4 roundup's Facebook / LinkedIn / Threads caption is built from the
    PICTURE parts ("The purchases: <every row>", "The largest of these: …" — news-v20's format-neutral wording; before,
    "The purchases shown", "The largest here") while the video names only the lead
    row's company. With no data card frozen (MARKETING_VIDEO_FEATURES without it, or a holder without video_v2) the
    build freezes no extra (WARNING naming `picture_caption`); with it frozen but the video drawn without it (the
    worker kept the text card: `data_card_slot` None) create_posts skips the extras (WARNING). Either way the day's
    primary posts are all recorded, and no video post claims rows its video does not show. Mutation-checked
    (2026-10-20): without `_video_draws_picture`'s asset check the second case records three extras."""
    sb, runs = tworld
    rec, day = _every_case(series)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    monkeypatch.setattr(ss.settings, "MARKETING_VIDEO_EXTRA_PLATFORMS", "facebook,linkedin,threads")
    frozen_card = how == "the_worker_kept_the_text_card"
    if frozen_card:
        monkeypatch.setattr(ss.settings, "MARKETING_VIDEO_FEATURES", "data_card,push_in")
    _today(monkeypatch, day)
    svc = _svc(runs, FakeNews({series: cands(series, rec)}))
    rid = _run(sb, day, caps=[*DROP2_CAPS, "video_v2"] if frozen_card else None)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_claim(sb, rid))
        await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == series
    out = _script(sb, rid)["output"]
    if not frozen_card:
        assert "extra_post_formats" not in out and "video_posts" not in out
        assert any("picture_caption" in r.getMessage() and rid in r.getMessage() for r in caplog.records)
        assert "The purchases: " in out["posts"]["facebook"]["caption"]     # the picture caption itself is untouched
    else:
        assert set(out["extra_post_formats"]) == {"facebook", "linkedin", "threads"}
    caplog.clear()
    video = _ready(sb, rid, "video", metadata={"data_card_slot": None, "video_features": ["data_card", "push_in"]}
                   if frozen_card else None)
    card = _ready(sb, rid, "card", image_role="post_image")
    _run_row(sb, rid)["metadata"].update({"video_asset_id": video, "image_asset_id": card})
    specs = [{"platform": p, "format": f, **({"asset_ids": [video if f == "video" else card]} if f != "text" else {})}
             for p, f in sorted(out["post_formats"].items())]
    with caplog.at_level(logging.WARNING, logger=mrs.logger.name):
        posts = await runs.create_posts(rid, specs, claim=_claim(sb, rid))
    assert [(p["platform"], p["format"]) for p in posts] == [(s["platform"], s["format"]) for s in specs]
    for p in posts:
        if p["format"] == "video":
            # no video post carries the picture's rows (the roundup's picture caption is the extras' caption)
            assert "The purchases: " not in p["caption"] and "The largest of these: " not in p["caption"]
    if frozen_card:
        skipped = [r for r in caplog.records if "SKIPPED" in r.getMessage() and r.levelno == logging.WARNING]
        assert len(skipped) == 3 and all("does not draw the picture" in r.getMessage() for r in skipped)


@pytest.mark.asyncio
async def test_batch3_with_the_switch_empty_a_template_day_records_exactly_its_primary_posts(tworld, monkeypatch):
    sb, runs = tworld
    _today(monkeypatch, MON)
    assert mrs.settings.MARKETING_VIDEO_EXTRA_PLATFORMS == ""            # `tworld` pins the declared default
    svc = _svc(runs, FakeNews({"ceo_buys": cands("ceo_buys", ceo_week())}))
    rid = _run(sb, MON)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted"
    out = _script(sb, rid)["output"]
    assert "extra_post_formats" not in out and "video_posts" not in out
    video, card = _ready(sb, rid, "video"), _ready(sb, rid, "card", image_role="post_image")
    _run_row(sb, rid)["metadata"].update({"video_asset_id": video, "image_asset_id": card})
    specs = [{"platform": p, "format": f, **({"asset_ids": [video if f == "video" else card]} if f != "text" else {})}
             for p, f in sorted(out["post_formats"].items())]
    posts = await runs.create_posts(rid, specs, claim=_claim(sb, rid))
    assert [(p["platform"], p["format"]) for p in posts] == [(s["platform"], s["format"]) for s in specs]


# ══ the spotlight lane (migration 210, 2026-11-15): a two-lane day, from the kick to create_posts ═══════════════


@pytest.mark.asyncio
async def test_spotlight_a_two_lane_day_posts_two_different_series_and_never_shares_a_key(tworld, monkeypatch):
    """One Saturday, two runs (UNIQUE (run_date, lane)). 10:00 — the spotlight run's chain is the spotlight calendar
    (the single-company pair, not listed in the switch, falls through to Money Map — the main Saturday is the lesson
    alone, so Money Map is the spotlight's to take, C0): Costco's Money Map is accepted on the spotlight lane, and
    create_posts records its posts under the lane's keys (`<date>:spotlight:<platform>:<format>`), never auto-approved.
    16:00 — the main run posts the week's lesson: the two posts of the day never share a series, a company or a key."""
    sb, runs = tworld
    for name, value in (("MARKETING_SPOTLIGHT_ENABLED", True), ("MARKETING_SPOTLIGHT_WEEKDAYS", "tue,thu,sat"),
                        ("MARKETING_SPOTLIGHT_HOUR_ET", 10), ("MARKETING_MAX_RUN_ATTEMPTS", 6),
                        ("MARKETING_RUN_HOUR_ET", 16)):
        monkeypatch.setattr(mrs.settings, name, value)
    _today(monkeypatch, SAT)
    spot_news = FakeNews({"money_map": cands("money_map", costco())})
    sid = _run(sb, SAT, lane="spotlight")
    spot = _svc(runs, spot_news)
    state = await spot.kick(sid, claim=_claim(sb, sid))
    assert state["status"] == "accepted" and state["template_id"] == "money_map"
    assert [c["series"] for c in spot_news.calls] == ["money_map"]
    row = _script(sb, sid)
    assert row["lane"] == "spotlight" and row["fact_sheet"]["selection"]["plan"] == "spotlight_13f_lead"
    posts = await runs.create_posts(sid, [{"platform": "x", "format": "text"}], claim=_claim(sb, sid))
    (post,) = posts
    assert post["idempotency_key"] == f"{SAT.isoformat()}:spotlight:x:text" and post["status"] == "pending_review"
    assert post["metadata"]["series"] == "money_map"
    # 16:00: the main run of the same day — the week's lesson, the news source never asked
    main_news = FakeNews({"money_map": cands("money_map", costco(company=co("WMT", "Walmart")))})
    writer = FakeWriter(["accepted"])
    mid = _run(sb, SAT)
    main = _svc(runs, main_news, writer)
    state = await main.kick(mid, claim=_claim(sb, mid))
    await _drain(main)
    assert state["template_id"] in selection.LESSON_TEMPLATE_IDS and main_news.calls == []
    assert "lane" not in _script(sb, mid) and _script(sb, mid)["source_ref"] != row["source_ref"]


@pytest.mark.asyncio
async def test_spotlight_an_off_season_thursday_posts_the_insider_spotlight_and_the_main_money_map(tworld,
                                                                                                  monkeypatch):
    """C0 (2026-11-16) end to end: on an off-season Thursday the main lane's chain is Money Map → lesson, so the
    spotlight's chain never holds Money Map — the 10:00 spotlight asks only its pair and skips `nothing_qualified`, and
    the 16:00 main run posts ITS Money Map (Walmart), recorded under the main keys: never a second weekly lesson."""
    sb, runs = tworld
    _open_spotlight_lane(monkeypatch)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "ceo_buys,insider_buys,thirteen_f,money_map,"
                                                               "insider_spotlight,thirteen_f_spotlight")
    thu = date(2026, 12, 3)
    _today(monkeypatch, thu)
    spot_news = FakeNews({"money_map": cands("money_map", costco())})
    sid = _run(sb, thu, lane="spotlight", caps=[*FEATURE_CAPS, "look_v23"])   # the insider list needs look_v23
    state = await _svc(runs, spot_news).kick(sid, claim=_claim(sb, sid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified"
    assert [c["series"] for c in spot_news.calls] == ["insider_spotlight", "thirteen_f_spotlight"]
    main_news = FakeNews({"money_map": cands("money_map", costco(company=co("WMT", "Walmart")))})
    mid = _run(sb, thu)
    main = _svc(runs, main_news)
    state = await main.kick(mid, claim=_claim(sb, mid))
    await _drain(main)
    assert state["status"] == "accepted" and state["template_id"] == "money_map"
    assert state["source_ref"] == "news:money_map:WMT:2025"
    posts = await runs.create_posts(mid, [{"platform": "x", "format": "text"}], claim=_claim(sb, mid))
    assert [p["idempotency_key"] for p in posts] == [f"{thu.isoformat()}:x:text"]


# ══ 2026-11-15: the `feature` layout's capability (layout_feature) — the LAYOUTS_2B rule for the spotlight picture ══
#
# FEATURE_LAYOUT_SERIES is derived from the templates' own specs: news-v22 shipped the two spotlight series on the
# `feature` layout, so it is exactly that pair. The gates are proven here on a stand-in series too (monkeypatched
# into the set): the chain drops it for a holder without `layout_feature` — on both lanes — and the write-time freeze
# makes a feature picture's posts TEXT under such a holder. The real pair end to end: the news-v22 section below.

FEATURE_CAPS = DROP2_CAPS + ["layout_feature"]


def _feature_out(out: Dict[str, Any]) -> Dict[str, Any]:
    """A composed template output whose picture is the `feature` layout, on its own first logo and footer."""
    out = copy.deepcopy(out)
    key, name = out["logos"][0]["key"], out["logos"][0]["name"]
    out["image_spec"] = {"layout": "feature", "version": 1, "kicker": "FILED LAST WEEK · FORM 4",
                         "footer": out["image_footer"], "header": {"logo": key, "name": name, "chip": key},
                         "figure": "$2M", "figure_label": "of stock, per the Form 4",
                         "headline": "A chief executive disclosed buying stock",
                         "facts": [{"label": "Shares", "value": "14K"},
                                   {"label": "Cost per share, as filed", "value": "$142.10–$143.85"}]}
    assert onscreen.validate_image_spec(out["image_spec"], onscreen.logo_keys(out["logos"]),
                                        footer=out["image_footer"]) is None
    return out


def test_the_feature_layout_series_are_derived_from_the_specs():
    """FEATURE_LAYOUT_SERIES = the series whose SeriesSpec names a WORKER_LAYOUTS_FEATURE layout (none until the
    spotlight templates land; then exactly those) — a shipped layout, disjoint from the drop-2b ones."""
    from app.schemas.marketing import (WORKER_CAPABILITIES, WORKER_CAPABILITY_LAYOUT_FEATURE, WORKER_LAYOUTS_2B,
                                       WORKER_LAYOUTS_FEATURE)

    assert WORKER_CAPABILITY_LAYOUT_FEATURE == "layout_feature" and WORKER_CAPABILITY_LAYOUT_FEATURE in WORKER_CAPABILITIES
    assert WORKER_LAYOUTS_FEATURE == ("feature",) and set(WORKER_LAYOUTS_FEATURE) <= set(onscreen.SHIPPED_LAYOUTS)
    assert not set(WORKER_LAYOUTS_FEATURE) & set(WORKER_LAYOUTS_2B)
    assert ss.FEATURE_LAYOUT_SERIES == {sid for sid, spec in news_templates.SERIES_SPECS.items()
                                        if spec.layout == "feature"}
    # news-v23 (2026-11-19): the 13F spotlight draws the ONE 13F list (`rows`); owner decisions 2026-11-20 ("3 - 5
    # companies at once", "Drop it from insider posts"): the insider spotlight is a `rows` list too — no series draws the
    # `feature` picture (the layout stays, with post_art, for a future art series)
    assert ss.FEATURE_LAYOUT_SERIES == frozenset()
    assert not ss.FEATURE_LAYOUT_SERIES & ss.LAYOUTS_2B_SERIES


@pytest.mark.asyncio
async def test_a_feature_layout_series_leaves_the_main_chain_of_a_holder_without_layout_feature(tworld, monkeypatch,
                                                                                               caplog):
    """A worker image older than the feature layout refuses its picture (a failed day): under such a holder the
    series whose picture is a feature layout leave the chain (ONE WARNING naming the worker and the capability), the
    rest of the chain runs as usual; under a `layout_feature` holder the same day asks for it."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    monkeypatch.setattr(ss, "FEATURE_LAYOUT_SERIES", frozenset({"ceo_buys"}))
    asked, warned = {}, {}
    for caps in (DROP2_CAPS, FEATURE_CAPS):
        news = FakeNews({})
        svc = _svc(runs, news)
        rid = _run(sb, MON, caps=caps)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=ss.logger.name):
            await svc.kick(rid, claim=_claim(sb, rid))
            await _drain(svc)
        asked[tuple(caps)] = [c["series"] for c in news.calls]
        warned[tuple(caps)] = [r.getMessage() for r in caplog.records
                               if "did not declare 'layout_feature'" in r.getMessage()]
    assert "ceo_buys" in asked[tuple(FEATURE_CAPS)] and "ceo_buys" not in asked[tuple(DROP2_CAPS)]
    assert asked[tuple(DROP2_CAPS)] == [s for s in asked[tuple(FEATURE_CAPS)] if s != "ceo_buys"]
    (msg,) = warned[tuple(DROP2_CAPS)]
    assert "ceo_buys" in msg and "feature" in msg and "'drop2a-test'" in msg and "(spotlight)" not in msg
    assert warned[tuple(FEATURE_CAPS)] == []


@pytest.mark.asyncio
async def test_with_no_feature_series_the_main_chain_is_unchanged_and_nothing_is_logged(tworld, monkeypatch, caplog):
    """No over-block (the real FEATURE_LAYOUT_SERIES: the spotlight pair, which no main-lane chain names): a holder
    without `layout_feature` asks for exactly the series a holder with it asks for, and nothing about the capability
    is logged."""
    sb, runs = tworld
    _today(monkeypatch, MON)
    assert ss.FEATURE_LAYOUT_SERIES <= {"insider_spotlight", "thirteen_f_spotlight"}
    asked = {}
    for caps in (DROP2_CAPS, FEATURE_CAPS):
        news = FakeNews({})
        svc = _svc(runs, news)
        rid = _run(sb, MON, caps=caps)
        with caplog.at_level(logging.WARNING, logger=ss.logger.name):
            await svc.kick(rid, claim=_claim(sb, rid))
            await _drain(svc)
        asked[tuple(caps)] = [c["series"] for c in news.calls]
    assert asked[tuple(DROP2_CAPS)] == asked[tuple(FEATURE_CAPS)] and asked[tuple(DROP2_CAPS)]
    assert not [r for r in caplog.records if "layout_feature" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_feature_layout_series_leaves_the_spotlight_chain_of_a_holder_without_layout_feature(tworld,
                                                                                                    monkeypatch, caplog):
    """The spotlight lane's half: its chain (Money Map standing in for a feature series) loses the series for a
    holder without `layout_feature` and SKIPS `nothing_qualified` — never the lesson, the source never asked — while
    a `layout_feature` holder's run reaches it."""
    sb, runs = tworld
    for name, value in (("MARKETING_SPOTLIGHT_ENABLED", True), ("MARKETING_SPOTLIGHT_WEEKDAYS", "tue,thu,sat"),
                        ("MARKETING_SPOTLIGHT_HOUR_ET", 10), ("MARKETING_MAX_RUN_ATTEMPTS", 6),
                        ("MARKETING_RUN_HOUR_ET", 16)):
        monkeypatch.setattr(mrs.settings, name, value)
    _today(monkeypatch, SAT)        # the main Saturday is the lesson alone: Money Map stays the spotlight's (C0)
    monkeypatch.setattr(ss, "FEATURE_LAYOUT_SERIES", frozenset({"money_map"}))
    news = FakeNews({"money_map": cands("money_map", costco())})
    sid = _run(sb, SAT, lane="spotlight", caps=DROP2_CAPS)
    svc = _svc(runs, news)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(sid, claim=_claim(sb, sid))
        await _drain(svc)
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified" and news.calls == []
    row = _script(sb, sid)
    assert row["status"] == "rejected" and row["reject_reason"] == "nothing_qualified"
    assert row.get("template_id") not in selection.LESSON_TEMPLATE_IDS
    warned = [r.getMessage() for r in caplog.records if "did not declare 'layout_feature'" in r.getMessage()]
    assert len(warned) == 1 and "(spotlight)" in warned[0] and "money_map" in warned[0] and sid in warned[0]
    # the same day, held by a worker that draws the feature layout, reaches the series
    sb.tables[mrs.SCRIPTS].rows.clear()
    sb.tables[mrs.RUNS].rows.clear()
    news = FakeNews({"money_map": cands("money_map", costco())})
    sid = _run(sb, SAT, lane="spotlight", caps=FEATURE_CAPS)
    state = await _svc(runs, news).kick(sid, claim=_claim(sb, sid))
    assert state["status"] == "accepted" and state["template_id"] == "money_map"


@pytest.mark.asyncio
async def test_a_feature_template_frozen_under_a_holder_without_layout_feature_is_text(tworld, caplog):
    """The freeze half (a re-claim by an older image during the build): a feature picture's platforms freeze as
    TEXT under a holder without `layout_feature` — `image_fallback` worker_capability, a WARNING naming the
    feature-layout worker — and as IMAGE under one that declared it."""
    sb, runs = tworld
    svc = _svc(runs, FakeNews({}))
    out = _feature_out(_composed())
    for caps, image in ((FEATURE_CAPS, True), (DROP2_CAPS, False), (["post_image", "news_templates"], False)):
        rid = _run(sb, MON, caps=caps)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=ss.logger.name):
            frozen = await svc._frozen_output(out, MON, run_id=rid, gen_id="g", image_posts=True)
        assert ("image" in frozen["post_formats"].values()) is image, caps
        assert frozen["image_footer"] == out["image_footer"]
        if not image:
            assert frozen["image_fallback"]["reason"] == "worker_capability"
            assert any("'layout_feature'" in r.getMessage() and "feature-layout" in r.getMessage()
                       for r in caplog.records), caps
    # a drop-2b worker still draws a `rows` picture as before (no over-block)
    rid = _run(sb, MON, caps=DROP2_CAPS)
    frozen = await svc._frozen_output(_composed(), MON, run_id=rid, gen_id="g", image_posts=True)
    assert "image" in frozen["post_formats"].values()


def test_a_feature_template_never_freezes_a_data_card():
    """Owner decision 5: the feature picture (the filing's cost per share among its facts) is never drawn into the
    video — `freeze_video_features` drops `data_card` for it (no picture the data card may draw), keeps push_in."""
    out = _feature_out(_composed())
    frozen = ss.freeze_post_formats(out, MON, image_posts=True, x_images=False, run_id="r")
    assert ss.freeze_video_features(frozen, switch=["data_card", "push_in"], holder_v2=True, run_id="r") == ["push_in"]
    plain = ss.freeze_post_formats(_composed(), MON, image_posts=True, x_images=False, run_id="r")
    assert "data_card" in ss.freeze_video_features(plain, switch=["data_card", "push_in"], holder_v2=True,
                                                    run_id="r")                         # anti-vacuity


# ══ news-v22 (2026-11-15): the spotlight pair, SHIPPED — each built on its spotlight day, drawn by the REAL worker,
# checked by the REAL server and recorded ═══════════════════════════════════════════════════════════════════════
#
# Listed in MARKETING_NEWS_SERIES, held by a worker that declared `layout_feature` (and `video_v2`, the data card
# asked for): each series leads its chain on its day (Thursday: the insider spotlight; the week's last spotlight day
# in the 13F season, Saturday: the 13F spotlight), freezes its `feature` picture as IMAGE posts, never freezes a data
# card (owner decision 5: the filing's cost per share is on the picture, and never reaches a video), and the real
# worker draws the picture at a step under the ceiling and a video of text cards only; the server accepts what the
# worker declares for both, and create_posts records the posts under the lane's keys, pending review.


def _spotlight_case(series: str):
    import test_marketing_news_templates as NT

    # news-v23 (2026-11-19): the 13F spotlight is the ONE 13F list (its five-holding sample)
    return {"insider_spotlight": lambda: (NT.isp_cluster(), NT.SPOT_THU),
            "thirteen_f_spotlight": lambda: (NT.f13s_list5(), NT.SPOT_SAT)}[series]()


def _open_spotlight_lane(monkeypatch) -> None:
    for name, value in (("MARKETING_SPOTLIGHT_ENABLED", True), ("MARKETING_SPOTLIGHT_WEEKDAYS", "tue,thu,sat"),
                        ("MARKETING_SPOTLIGHT_HOUR_ET", 10), ("MARKETING_MAX_RUN_ATTEMPTS", 6),
                        ("MARKETING_RUN_HOUR_ET", 16)):
        monkeypatch.setattr(mrs.settings, name, value)


# news-v23 (2026-11-19): the 13F spotlight draws the ONE 13F list — its end to end is
# `test_f13s_list_builds_draws_and_records_end_to_end` below
@pytest.mark.parametrize("series", ["insider_spotlight"])
@pytest.mark.asyncio
async def test_news_v22_each_spotlight_series_builds_draws_and_records_end_to_end(tworld, monkeypatch, caplog,
                                                                                  series):
    pytest.importorskip("PIL")
    from marketing import cards
    from marketing import news_layouts as nl

    sb, runs = tworld
    rec, day = _spotlight_case(series)
    _open_spotlight_lane(monkeypatch)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    monkeypatch.setattr(ss.settings, "MARKETING_VIDEO_FEATURES", "data_card,push_in")
    _today(monkeypatch, day)
    news = FakeNews({series: cands(series, rec)})
    svc = _svc(runs, news)
    caps = [*FEATURE_CAPS, "video_v2", "look_v23"]           # owner decisions 2026-11-20: the insider list is look_v23's
    rid = _run(sb, day, lane="spotlight", caps=caps)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == series
    assert [c["series"] for c in news.calls] == [series]                     # it leads its chain that day
    row = _script(sb, rid)
    out = row["output"]
    assert row["lane"] == "spotlight" and row["fact_sheet"]["selection"]["chain"][0] == series
    assert out["template_version"] == news_templates.TEMPLATE_VERSION == "news-v23"
    # owner decisions 2026-11-20 ("3 - 5 companies at once"): the insider LIST — a `rows` picture of three companies
    assert out["series"] == series and out["content_class"] == "C" and out["image_spec"]["layout"] == "rows"
    assert len(out["image_spec"]["sections"][0]["rows"]) == len(rec.companies) >= 3
    assert news_templates.revalidate(out, fact_sheet=row["fact_sheet"], run_date=day) == []
    assert "image" in out["post_formats"].values() and "image_fallback" not in out
    assert out["video_features"] == ["push_in"]                              # the data card is never frozen
    # the read-back as it crosses the wire: no data-card slot, and the worker draws text cards only
    sent = json.loads(WorkerScript(**state["script"]).model_dump_json())
    assert sent.get("data_card_slot") is None and "data_card" not in (sent.get("video_features") or [])
    with caplog.at_level(logging.WARNING, logger="marketing.cards"):
        specs = cards.cards_for_script(sent, font_path=_WORKER_FONT, layout_engine="basic")
    assert [s.kind for s in specs] == ["opening", "text", "text", "text", "text", "disclaimer"]
    drawn = list(dict.fromkeys(t for s in specs for t in cards.onscreen_strings(s)))
    await runs._check_onscreen_text(_run_row(sb, rid), {"onscreen_text": drawn, "voice_asset_id": _voice(sb, rid)})
    # the picture: the real worker lays out the list under the ceiling, and the server accepts exactly its strings
    image = nl.template_image_for_script(sent, cards.logo_table(sent, {}))
    layout = nl.layout_template(image, font_path=_WORKER_FONT, layout_engine="basic")
    assert layout.kind == "template:rows" and layout.step <= cards.RAMP_STEPS - 1 and layout.art is None
    picture = onscreen.image_strings(out["image_spec"], out["logos"])
    runs._check_template_image_text(rid, out, picture, capabilities=mrs.run_worker_capabilities(_run_row(sb, rid)))
    # owner decision 5: the filing's cost per share is on the picture rows' detail lines, in no video string (the
    # compact cover draws no detail line) and in no spoken word
    import test_marketing_news_templates as NT
    costs = NT._cost_strings(rec)
    video_words = " ".join([sent["hook"], *sent["video_script"], *drawn])
    assert any(c in t for c in costs for t in picture) and any(t.endswith("a share, as filed") for t in picture)
    assert not [c for c in costs if c in video_words]
    assert not re.search(r"\bas filed\b|\ba share\b", video_words)
    # create_posts: the lane's keys, the series, pending review — the video posts with their frozen features
    video, card = _ready(sb, rid, "video"), _ready(sb, rid, "card", image_role="post_image")
    _run_row(sb, rid)["metadata"].update({"video_asset_id": video, "image_asset_id": card})
    specs_in = [{"platform": p, "format": f, **({"asset_ids": [video if f == "video" else card]} if f != "text" else {})}
                for p, f in sorted(out["post_formats"].items())]
    posts = await runs.create_posts(rid, specs_in, claim=_claim(sb, rid))
    assert posts and {p["status"] for p in posts} == {"pending_review"}
    assert all(p["idempotency_key"].startswith(f"{day.isoformat()}:spotlight:") for p in posts)
    assert {p["metadata"]["series"] for p in posts} == {series} and "image" in {p["format"] for p in posts}
    for p in posts:
        assert p["metadata"].get("video_features") == (["push_in"] if p["format"] == "video" else None)


@pytest.mark.asyncio
async def test_f13s_list_builds_draws_and_records_end_to_end(tworld, monkeypatch, caplog):
    """Owner feedback 2026-11-19 ("show a list of what Fabrikam Capital did … not just one"): a 13F spotlight of five
    holdings builds on its Saturday, freezes its `rows` picture as IMAGE posts (no data card: the series has no slot),
    the REAL worker draws the picture (five rows, every figure signed) under the ceiling and the cover with the
    FILER's wordmark plate — no logo fetched for it — and a video of text cards; the server accepts exactly what the
    worker declares, and create_posts records the posts under the lane's keys, pending review."""
    pytest.importorskip("PIL")
    import test_marketing_news_templates as NT
    from marketing import cards
    from marketing import news_layouts as nl

    sb, runs = tworld
    rec, day = NT.f13s_list5(), NT.SPOT_SAT
    _open_spotlight_lane(monkeypatch)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    monkeypatch.setattr(ss.settings, "MARKETING_VIDEO_FEATURES", "data_card,push_in")
    _today(monkeypatch, day)
    news = FakeNews({"thirteen_f_spotlight": cands("thirteen_f_spotlight", rec)})
    svc = _svc(runs, news)
    rid = _run(sb, day, lane="spotlight", caps=[*FEATURE_CAPS, "video_v2"])
    with caplog.at_level(logging.INFO, logger=ss.__name__):
        state = await svc.kick(rid, claim=_claim(sb, rid))
        await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == "thirteen_f_spotlight"
    row = _script(sb, rid)
    out = row["output"]
    assert out["image_spec"]["layout"] == "rows" and len(out["image_spec"]["sections"][0]["rows"]) == 5
    assert row["source_ref"] == "news:thirteen_f_spotlight:0009999902:2026-Q3:NWT"           # the lead's key
    # the cross-lane rule (b) compares LEADS: the list's lead is its first (largest) row, never the filer's plate
    assert ss.lead_company(out) == "NWT" and ss.lead_company_name(out) == "Northwind"
    assert ss.lead_keys(out) == {ss.LEAD_KEY_ROOT + "NWT", ss.LEAD_KEY_NAME + "northwind"}
    assert news_templates.revalidate(out, fact_sheet=row["fact_sheet"], run_date=day) == []
    assert "image" in out["post_formats"].values() and out["video_features"] == ["push_in"]
    # the filer's plate: a wordmark by design — never a logo fetch for a key that is no ticker
    filer = next(e for e in out["logos"] if e["key"] == news_templates.F13S_FILER_LOGO_KEY)
    assert filer == {**filer, "name": "Fabrikam Capital", "url": None, "sha256": None}
    assert sorted(news.logo_calls) == sorted(m.company.symbol for m in rec.moves)          # the holdings' alone
    assert "names no listing" in caplog.text
    sent = json.loads(WorkerScript(**state["script"]).model_dump_json())
    assert sent.get("data_card_slot") is None
    specs = cards.cards_for_script(sent, font_path=_WORKER_FONT, layout_engine="basic")
    assert [s.kind for s in specs] == ["opening", "text", "text", "text", "text", "disclaimer"]
    drawn = list(dict.fromkeys(t for s in specs for t in cards.onscreen_strings(s)))
    assert "Fabrikam Capital" in drawn                                                       # the wordmark plate
    await runs._check_onscreen_text(_run_row(sb, rid), {"onscreen_text": drawn, "voice_asset_id": _voice(sb, rid)})
    image = nl.template_image_for_script(sent, cards.logo_table(sent, {}))
    layout = nl.layout_template(image, font_path=_WORKER_FONT, layout_engine="basic")
    assert layout.kind == "template:rows" and layout.step <= cards.RAMP_STEPS - 1 and layout.art is None
    picture = onscreen.image_strings(out["image_spec"], out["logos"])
    assert {"−18.1M", "+5.6M", "+4.5M", "−4.3M", "−1M"} <= set(picture)
    runs._check_template_image_text(rid, out, picture, capabilities=mrs.run_worker_capabilities(_run_row(sb, rid)))
    video, card = _ready(sb, rid, "video"), _ready(sb, rid, "card", image_role="post_image")
    _run_row(sb, rid)["metadata"].update({"video_asset_id": video, "image_asset_id": card})
    specs_in = [{"platform": p, "format": f, **({"asset_ids": [video if f == "video" else card]} if f != "text" else {})}
                for p, f in sorted(out["post_formats"].items())]
    posts = await runs.create_posts(rid, specs_in, claim=_claim(sb, rid))
    assert posts and {p["status"] for p in posts} == {"pending_review"}
    assert {p["metadata"]["series"] for p in posts} == {"thirteen_f_spotlight"} and "image" in {p["format"] for p in posts}


@pytest.mark.asyncio
async def test_news_v22_a_spotlight_series_not_listed_is_never_asked_and_its_day_falls_through(tworld, monkeypatch):
    """Shipped is not on: with the per-series switch at its declared default (the 2a four) the spotlight day's chain
    never asks for the pair — Money Map, then the skip, on the Saturday (whose main day is the lesson) — whatever the
    holder can draw; and on the Thursday, whose main chain holds Money Map, there is no spotlight day at all (C0: the
    claim answers not_scheduled, a run claimed before rests)."""
    sb, runs = tworld
    _open_spotlight_lane(monkeypatch)
    _today(monkeypatch, SAT)
    news = FakeNews({})
    rid = _run(sb, SAT, lane="spotlight", caps=[*FEATURE_CAPS, "video_v2"])
    state = await _svc(runs, news).kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified"
    assert [c["series"] for c in news.calls] == ["money_map"]
    assert not selection.is_spotlight_day(THU)
    _today(monkeypatch, THU)
    news = FakeNews({})
    rid = _run(sb, THU, lane="spotlight", caps=[*FEATURE_CAPS, "video_v2"])
    state = await _svc(runs, news).kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "rest_day" and news.calls == []


# ══ news-v22 (B5): the first-line prefixes, from the build to create_posts — never to the worker ══════════════════════


@pytest.mark.parametrize("series, prefix_id", [("insider_spotlight", "insider.breaking"),
                                               ("thirteen_f_spotlight", "thirteen_f.filing")])
@pytest.mark.asyncio
async def test_news_v22_a_prefixed_spotlight_day_carries_its_prefix_in_captions_only(tworld, monkeypatch, series,
                                                                                     prefix_id):
    """MARKETING_FIRST_LINE_PREFIX on at the build: each spotlight's stored captions open on its prefix where it fits
    ("🚨 BREAKING:" — every person of the insider list filed the business day before the Thursday run, owner decisions
    2026-11-20; "🐋 13F filing:"), the output
    re-checks clean as stored, the worker's read-back carries no prefix and no emoji at all (the worker never receives
    captions: the video, its cards and the cover stay prefix-free), and create_posts records the prefixed captions
    under the lane's keys, pending review."""
    import test_marketing_news_templates as NT

    sb, runs = tworld
    rec, day = _spotlight_case(series)
    if series == "insider_spotlight":
        rec = NT.isp_n(3)                       # every company filed the business day before the run: BREAKING
    _open_spotlight_lane(monkeypatch)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    monkeypatch.setattr(mrs.settings, "MARKETING_FIRST_LINE_PREFIX", True)
    _today(monkeypatch, day)
    news = FakeNews({series: cands(series, rec)})
    svc = _svc(runs, news)
    rid = _run(sb, day, lane="spotlight", caps=[*FEATURE_CAPS, "video_v2", "look_v23"])
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == series
    row = _script(sb, rid)
    out = row["output"]
    prefix = news_templates.FIRST_LINE_PREFIXES[prefix_id]
    assert out["first_line_prefix_on"] is True and out["first_line_prefix"] == prefix
    assert out["captions"]["facebook"].startswith(f"{prefix} ") and out["posts"]["facebook"]["caption"].startswith(
        f"{prefix} ")
    assert not out["captions"]["youtube_title"].startswith(prefix)
    assert news_templates.revalidate(out, fact_sheet=row["fact_sheet"], run_date=day) == []
    sent = json.loads(WorkerScript(**state["script"]).model_dump_json())
    wire = json.dumps(sent, ensure_ascii=False)
    assert prefix not in wire and "BREAKING" not in wire and "13F filing:" not in wire
    assert not re.search("[\U0001F000-\U0001FFFF]", wire)
    monkeypatch.setattr(mrs.settings, "MARKETING_FIRST_LINE_PREFIX", False)       # the frozen value still rules
    specs = [{"platform": p, "format": "text"} for p, f in sorted(out["post_formats"].items()) if f == "text"]
    assert specs                                                                   # X posts text
    posts = await runs.create_posts(rid, specs, claim=_claim(sb, rid))
    assert {p["status"] for p in posts} == {"pending_review"}
    for p in posts:
        assert p["caption"] == out["posts"][p["platform"]]["caption"]
        assert p["idempotency_key"].startswith(f"{day.isoformat()}:spotlight:")


# ══ Post art (workstream C, slice C3, 2026-11-16): the build's art step, the read-back, create_posts ════════════════
#
# Owner decisions 2026-11-15 — "Google draws, Claude checks", "Picture and video cover", main lane "Video cover now,
# pictures later". The art step runs inside the template build AFTER the record is chosen, composed, re-checked and
# frozen, BEFORE the one INSERT (`_attach_art`): gated on the switch (read at the step), the monthly budget, the
# single-company record (decision 4), a picture or video post on the day, and the holder's `post_art` — read from the
# freeze's ONE holder read. Art never refuses, skips or fails a day. Real `post_art.make_post_art`, real Storage fake,
# fake Claude / image clients (tests/test_marketing_post_art.py's): no network, no model.

from app.integrations import anthropic_client as ac  # noqa: E402
from app.services import llm_provider as lp  # noqa: E402
from app.services.marketing import post_art as pa  # noqa: E402
from test_marketing_post_art import (FAIL_TEXT, PASS, SHIPPED_ART_SERIES, SHIPPED_COVER_ONLY_SERIES,  # noqa: E402
                                     SHIPPED_NEVER_ART_SERIES, FakeClaude, FakeGemini, keep_art_on_money_map,
                                     spot_output)

#: What the per-post-art worker declares (marketing/main.py, once its drawing lands): every capability.
ART_CAPS = [*FEATURE_CAPS, "video_v2", "post_art"]


@pytest.fixture
def art_world(tworld, monkeypatch):
    lp.reset_breaker()
    monkeypatch.setattr(lp, "_logged_once", set())
    monkeypatch.setattr(pa, "_logged_once", set())
    for name, value in {
        "MARKETING_ART_ENABLED": True, "MARKETING_ART_MONTHLY_BUDGET_USD": 30.0, "MARKETING_ART_BUDGET_SECONDS": 180,
        "MARKETING_ART_CLAUDE_MODEL": "claude-opus-5-5", "MARKETING_ART_IMAGE_MODEL": "gemini-3-pro-image",
        "MARKETING_ART_IMAGE_MICROS": 134_000, "CAYDEX_ANTHROPIC_API_KEY": "sk-ant-test",
        "CAYDEX_CLAUDE_MAX_TOKENS": 16000, "CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS": 120.0,
        "CAYDEX_CLAUDE_BREAKER_SECONDS": 3600.0, "MARKETING_MEDIA_BUCKET": "marketing-media",
    }.items():
        monkeypatch.setattr(mrs.settings, name, value)
    # owner decision 2026-11-20 ("Drop it from insider posts (Recommended) :: Keep the code but use it nowhere for
    # now"): no shipped series carries art. These tests exercise the KEPT code on the Money Map, put back into the art
    # set for the test only; the shipped sets' own day is test_post_art_as_shipped_… below.
    keep_art_on_money_map(monkeypatch)
    yield tworld
    lp.reset_breaker()


class FactsNews(FakeNews):
    """The adapter's `company_facts` too (B decision 8): name-free sector / industry, recorded per call."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.facts_calls: List[tuple] = []

    async def company_facts(self, symbol, *, timeout):
        self.facts_calls.append((symbol, timeout))
        return {"sector": "Technology", "industry": "Software Infrastructure"}


async def _art_day(sb, runs, monkeypatch, series: str = "money_map", *, caps=None, claude=None, gemini=None,
                   news=None, kick: bool = True) -> SimpleNamespace:
    """One day of `series` held by `caps` (ART_CAPS). Owner decisions 2026-11-20 ("Drop it from insider posts
    (Recommended)"): the default is the ONE art series left — a main-lane MONDAY's Money Map (Costco; the Form 4 pair
    and the theme come up empty before it), its art on the video cover only; a spotlight series runs on its own
    spotlight day (the lane open, every series listed)."""
    if series == "money_map":
        rec, day, lane = costco(), MON, None
    else:
        (rec, day), lane = _spotlight_case(series), "spotlight"
        _open_spotlight_lane(monkeypatch)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    _today(monkeypatch, day)
    news = news or FakeNews({series: cands(series, rec)})
    gemini, claude = gemini or FakeGemini(), claude or FakeClaude()
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news, art_gemini=gemini,
                                    art_claude=claude)
    rid = _run(sb, day, caps=ART_CAPS if caps is None else caps, **({"lane": lane} if lane else {}))
    state = None
    if kick:
        state = await svc.kick(rid, claim=_claim(sb, rid))
        await _drain(svc)
    return SimpleNamespace(svc=svc, rid=rid, state=state, news=news, gemini=gemini, claude=claude, rec=rec, day=day)


def _mm_calls(day) -> int:
    """How many times the day's builds asked for the Money Map (a Monday asks the Form 4 pair and the theme first)."""
    return len([c for c in day.news.calls if c["series"] == "money_map"])


def _art_objects(sb) -> Dict[str, tuple]:
    return {p: v for p, v in sb.storage.objects.items() if p.startswith("art/")}


# ── the switch off: today's day, byte for byte, and no model call ─────────────


@pytest.mark.asyncio
async def test_post_art_with_the_switch_off_the_day_is_todays_and_no_model_is_called(tworld, monkeypatch):
    sb, runs = tworld
    day = await _art_day(sb, runs, monkeypatch)
    assert day.state["status"] == "accepted"
    out = _script(sb, day.rid)["output"]
    assert "art" not in out and day.state["script"]["art"] is None
    assert day.gemini.calls == [] and day.claude.calls == [] and day.gemini.text_calls == []
    assert _art_objects(sb) == {}
    assert news_templates.revalidate(out, fact_sheet=_script(sb, day.rid)["fact_sheet"], run_date=day.day) == []


@pytest.mark.parametrize("image_posts", [True, False])
@pytest.mark.asyncio
async def test_post_art_the_switch_adds_no_holder_read(art_world, monkeypatch, image_posts):
    """The art gate reads the capability from the freeze's ONE holder read (`get_run` counted on the whole day): with
    image posts on, the switch adds NO read; with image posts and video features off (no read of their own), the
    freeze makes that one read for the art."""
    sb, runs = art_world
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_POSTS", image_posts)
    monkeypatch.setattr(mrs.settings, "MARKETING_VIDEO_FEATURES", "")
    counts = {}
    for on in (False, True):
        monkeypatch.setattr(mrs.settings, "MARKETING_ART_ENABLED", on)
        sb.tables[mrs.SCRIPTS].rows.clear()
        sb.tables[mrs.RUNS].rows.clear()
        reads: List[str] = []
        real = runs.get_run

        async def counting(run_id, real=real, reads=reads):
            reads.append(run_id)
            return await real(run_id)

        monkeypatch.setattr(runs, "get_run", counting)
        day = await _art_day(sb, runs, monkeypatch)
        monkeypatch.setattr(runs, "get_run", real)
        assert day.state["status"] == "accepted"
        counts[on] = len(reads)
        assert ("art" in _script(sb, day.rid)["output"]) is on
    assert counts[True] == counts[False] + (0 if image_posts else 1)    # the art gate's read IS the freeze's


# ── on: the Money Map pictures its company on the video cover ──
# Owner decisions 2026-11-20 ("Drop it from insider posts (Recommended)"): the insider spotlight is a LIST of 3-5
# companies and never takes art (`test_post_art_an_insider_list_gets_no_art_and_spends_nothing` below); the 13F
# spotlight is the ONE 13F list and never takes art either — the Money Map's cover is the one art surface left.


@pytest.mark.parametrize("series, symbol, name", [("money_map", "COST", "Costco")])
@pytest.mark.asyncio
async def test_post_art_a_money_map_day_records_checked_art_and_the_read_back_carries_it(art_world, monkeypatch,
                                                                                        series, symbol, name):
    sb, runs = art_world
    day = await _art_day(sb, runs, monkeypatch, series)
    assert day.state["status"] == "accepted" and day.state["template_id"] == series
    row = _script(sb, day.rid)
    out = row["output"]
    art = out["art"]
    assert art["status"] == "ok" and art["reason"] is None and art["version"] == pa.ART_VERSION
    assert art["company"] == {"symbol": symbol, "name": name}
    assert art["surfaces"] == ["cover"] and art["palette"] == "blueprints"            # "Video cover now"
    assert pa.drawable_art(out) is not None and art["url"] == pa.expected_art_url(art["sha256"])
    # stored content-addressed in the public bucket, exactly the checked bytes, no asset row
    ((path, (data, ctype)),) = _art_objects(sb).items()
    assert path == pa.art_path(art["sha256"]) and ctype == "image/jpeg"
    assert hashlib.sha256(data).hexdigest() == art["sha256"] and len(data) == art["bytes"]
    assert not [a for a in sb.tables[mrs.ASSETS].rows if a.get("run_id") == day.rid]
    # one brief, one draw, one check; never a Gemini text call; the spend is recorded on the row it belongs to
    assert day.claude.tags() == [pa.BRIEF_USAGE_TAG, pa.CHECK_USAGE_TAG] and len(day.gemini.calls) == 1
    assert day.gemini.text_calls == []
    assert art["usage"]["images"] == 1 and art["usage"]["estimated_micros"] > 134_000
    # template rows keep 0 tokens and no claude_usage: the art's spend lives in output.art only
    assert row["tokens_used"] == 0 and row.get("claude_usage") is None and row["model"] is None
    # the stored output still re-checks exactly as create_posts will (art is outside the compared fields)
    assert news_templates.revalidate(out, fact_sheet=row["fact_sheet"], run_date=day.day) == []
    # the read-back: the five keys, nothing else
    sent = json.loads(WorkerScript(**day.state["script"]).model_dump_json())
    assert sent["art"] == {k: art[k] for k in pa.WORKER_ART_KEYS}
    assert "subject" not in json.dumps(sent["art"]) and "claude" not in json.dumps(sent).lower()


@pytest.mark.asyncio
async def test_post_art_as_shipped_a_profit_year_money_map_day_spends_nothing_with_the_switch_on(art_world,
                                                                                                monkeypatch):
    """Owner decision 2026-11-20 ("Drop it from insider posts (Recommended) :: Keep the code but use it nowhere for now;
    the switch stays off" — "Put it on Money Map covers" declined): with the SHIPPED art sets, MARKETING_ART_ENABLED ON,
    a budget and a worker that declares `post_art`, a PROFIT-year Money Map day — the very day the kept code was built
    for — records `not_a_company_series` before any spend: no brief, no drawing, no stored object, no art in the
    read-back; the day posts as usual. Mutation-checked by hand: `COVER_ONLY_SERIES = ("money_map",)` made, stored and
    paid for cover art."""
    sb, runs = art_world
    for name, value in (("COVER_ONLY_SERIES", SHIPPED_COVER_ONLY_SERIES), ("ART_SERIES", SHIPPED_ART_SERIES),
                        ("NEVER_ART_SERIES", SHIPPED_NEVER_ART_SERIES)):
        monkeypatch.setattr(pa, name, value)
    assert mrs.settings.MARKETING_ART_ENABLED is True
    day = await _art_day(sb, runs, monkeypatch)
    assert day.state["status"] == "accepted" and day.state["template_id"] == "money_map"
    row = _script(sb, day.rid)
    out = row["output"]
    assert "costs" not in out["opening_card"]                                     # anti-vacuity: a PROFIT year
    art = out["art"]
    assert (art["status"], art["reason"]) == ("failed", "not_a_company_series")
    assert pa.estimated_micros(art["usage"]) == 0 and pa.drawable_art(out) is None
    assert day.claude.calls == [] and day.gemini.calls == [] and _art_objects(sb) == {}
    assert day.state["script"]["art"] is None
    assert news_templates.revalidate(out, fact_sheet=row["fact_sheet"], run_date=day.day) == []


@pytest.mark.asyncio
async def test_post_art_a_13f_spotlight_list_gets_no_art_and_spends_nothing(art_world, monkeypatch):
    """Owner feedback 2026-11-19: a 13F spotlight LIST (five holdings) is not one company — the art step records
    `not_a_company_series` before any spend (no brief, no drawing, no stored object), the worker's read-back carries no
    art, and the stored output still re-checks."""
    import test_marketing_news_templates as NT

    sb, runs = art_world
    rec = NT.f13s_list5()
    news = FakeNews({"thirteen_f_spotlight": cands("thirteen_f_spotlight", rec)})
    day = await _art_day(sb, runs, monkeypatch, "thirteen_f_spotlight", news=news)
    assert day.state["status"] == "accepted" and day.state["template_id"] == "thirteen_f_spotlight"
    row = _script(sb, day.rid)
    art = row["output"]["art"]
    assert (art["status"], art["reason"]) == ("failed", "not_a_company_series")
    assert pa.estimated_micros(art["usage"]) == 0 and pa.drawable_art(row["output"]) is None
    assert day.claude.calls == [] and day.gemini.calls == [] and _art_objects(sb) == {}
    assert day.state["script"]["art"] is None
    assert news_templates.revalidate(row["output"], fact_sheet=row["fact_sheet"], run_date=day.day) == []


@pytest.mark.asyncio
async def test_post_art_a_loss_year_money_map_gets_no_art_its_cover_is_the_costs_card(art_world, monkeypatch):
    """Owner decision 2026-11-20 ("A: costs vs revenue"): a Money Map LOSS year's video cover is its COSTS card, which
    draws no art — the art step records `costs_cover` before any spend (no brief, no drawing, no stored object), the
    read-back carries no art, the stored output re-checks; a PROFIT year's Money Map still gets its cover art (the test
    above). Mutation-checked by hand: without the gate the day made, stored and paid for art nobody would draw."""
    import test_marketing_news_templates as NT

    sb, runs = art_world
    news = FakeNews({"money_map": cands("money_map", NT.rivian())})
    day = await _art_day(sb, runs, monkeypatch, news=news, caps=[*ART_CAPS, "look_v23"])
    assert day.state["status"] == "accepted" and day.state["template_id"] == "money_map"
    row = _script(sb, day.rid)
    assert "costs" in row["output"]["opening_card"]                                      # anti-vacuity: a loss year
    art = row["output"]["art"]
    assert (art["status"], art["reason"]) == ("failed", "costs_cover") and art["company"]["symbol"] == "RIVN"
    assert pa.estimated_micros(art["usage"]) == 0 and pa.drawable_art(row["output"]) is None
    assert day.claude.calls == [] and day.gemini.calls == [] and _art_objects(sb) == {}
    assert day.state["script"]["art"] is None
    assert news_templates.revalidate(row["output"], fact_sheet=row["fact_sheet"], run_date=day.day) == []


@pytest.mark.asyncio
async def test_post_art_an_insider_list_gets_no_art_and_spends_nothing(art_world, monkeypatch):
    """Owner decisions 2026-11-20 ("Drop it from insider posts (Recommended)"; "3 - 5 companies at once"): the insider
    spotlight is a LIST of companies — never art: the step records `not_a_company_series` before any spend (no brief,
    no drawing, no stored object), the read-back carries no art, and the stored output re-checks."""
    sb, runs = art_world
    day = await _art_day(sb, runs, monkeypatch, "insider_spotlight", caps=[*ART_CAPS, "look_v23"])
    assert day.state["status"] == "accepted" and day.state["template_id"] == "insider_spotlight"
    row = _script(sb, day.rid)
    art = row["output"]["art"]
    assert (art["status"], art["reason"]) == ("failed", "not_a_company_series")
    assert pa.estimated_micros(art["usage"]) == 0 and pa.drawable_art(row["output"]) is None
    assert day.claude.calls == [] and day.gemini.calls == [] and _art_objects(sb) == {}
    assert day.state["script"]["art"] is None
    assert news_templates.revalidate(row["output"], fact_sheet=row["fact_sheet"], run_date=day.day) == []


@pytest.mark.asyncio
async def test_post_art_the_brief_reads_the_name_free_company_facts_inside_the_art_budget(art_world, monkeypatch):
    sb, runs = art_world
    news = FactsNews({"money_map": cands("money_map", costco())})
    day = await _art_day(sb, runs, monkeypatch, news=news)
    assert news.facts_calls == [("COST", pa.STEP_SECONDS["facts"][1])]
    brief = day.claude.calls[0][0]
    assert "Software Infrastructure" in brief and "United States" in brief           # sector / industry / top line
    assert "COST" not in brief and "$" not in brief.split("Describe ONE")[0]         # never a ticker or a figure
    assert _script(sb, day.rid)["output"]["art"]["facts"]["industry"] == "Software Infrastructure"


# ── the main lane: the cover only, single-company records only ────────────────


@pytest.mark.asyncio
async def test_post_art_the_main_lane_pictures_a_single_company_on_the_cover_only(art_world, monkeypatch):
    sb, runs = art_world
    _today(monkeypatch, MON)
    news = FakeNews({"ceo_buys": None, "insider_buys": None, "money_map": cands("money_map", costco())})
    gemini, claude = FakeGemini(), FakeClaude()
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news, art_gemini=gemini,
                                    art_claude=claude)
    rid = _run(sb, MON, caps=[*DROP2_CAPS, "video_v2", "post_art"])
    state = await svc.kick(rid, claim=_claim(sb, rid))
    assert state["status"] == "accepted" and state["template_id"] == "money_map"
    out = _script(sb, rid)["output"]
    assert out["art"]["status"] == "ok" and out["art"]["surfaces"] == ["cover"]       # "Video cover now"
    assert out["art"]["company"] == {"symbol": "COST", "name": "Costco"}
    assert out["image_spec"]["layout"] != "feature"                                   # its picture is today's
    assert state["script"]["art"]["sha256"] == out["art"]["sha256"]


@pytest.mark.asyncio
async def test_post_art_a_multi_company_roundup_gets_no_art_and_spends_nothing(art_world, monkeypatch, caplog):
    sb, runs = art_world
    _today(monkeypatch, MON)
    news = FakeNews({"ceo_buys": cands("ceo_buys", ceo_week())})                     # Lowe's AND Starbucks
    gemini, claude = FakeGemini(), FakeClaude()
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news, art_gemini=gemini,
                                    art_claude=claude)
    rid = _run(sb, MON, caps=[*DROP2_CAPS, "post_art"])
    with caplog.at_level(logging.INFO):
        state = await svc.kick(rid, claim=_claim(sb, rid))
    art = _script(sb, rid)["output"]["art"]
    assert state["status"] == "accepted" and (art["status"], art["reason"]) == ("failed", "not_a_company_series")
    assert art["usage"]["estimated_micros"] == 0 and gemini.calls == [] and claude.calls == []
    assert state["script"]["art"] is None and _art_objects(sb) == {}
    assert any("MARKETING_ART" in r.getMessage() and "not_a_company_series" in r.getMessage() for r in caplog.records)


# ── every way to no art: the day goes on, nothing spent ───────────────────────


def _seed_month_spend(sb, micros: Any, *, day: str = "2026-11-02") -> None:
    usage = {"images": 0, "images_uncertain": 0, "image_micros": 134_000, "claude": {}, "claude_uncertain": {},
             "estimated_micros": micros}
    sb.tables[mrs.SCRIPTS].rows.append({"run_id": str(uuid.uuid4()), "run_date": day, "status": "rejected",
                                        "output": {"art": {"status": "failed", "usage": usage}}})


@pytest.mark.parametrize("case, reason", [
    ("no_capability", "no_capability"),
    ("budget_cap", "budget_cap"),
    ("budget_unreadable", "budget_unreadable"),
    ("claude_unpriced", "claude_unpriced"),
    ("timeout", "timeout"),
    ("holder_unreadable", "ledger_unreadable"),
    ("budget_zero", None),
])
@pytest.mark.asyncio
async def test_post_art_each_gate_is_no_art_before_any_spend_and_the_day_goes_on(art_world, monkeypatch, caplog,
                                                                                  case, reason):
    sb, runs = art_world
    caps = None
    if case == "no_capability":
        caps = [*FEATURE_CAPS, "video_v2"]
    elif case == "budget_cap":
        cap = 30_000_000
        _seed_month_spend(sb, cap - pa.run_worst_micros() + 1)                # one micro past: refused
    elif case == "budget_unreadable":
        _seed_month_spend(sb, None)                                           # a record whose spend was lost
    elif case == "claude_unpriced":
        monkeypatch.setattr(mrs.settings, "MARKETING_ART_CLAUDE_MODEL", "claude-unknown-9")
    elif case == "timeout":
        monkeypatch.setattr(ss, "TEMPLATE_DAY_BUDGET_SECONDS", 10.0)          # the build left no room for art
    elif case == "budget_zero":
        monkeypatch.setattr(mrs.settings, "MARKETING_ART_MONTHLY_BUDGET_USD", 0.0)
    if case == "holder_unreadable":
        async def no_holder(self, run_id):
            return None, mrs.MarketingRunError("PostgREST 503")

        monkeypatch.setattr(ss.MarketingScriptService, "_read_holder", no_holder)
    with caplog.at_level(logging.WARNING):
        day = await _art_day(sb, runs, monkeypatch, caps=caps)
    assert day.state["status"] == "accepted"                                  # never a skipped or failed day
    out = _script(sb, day.rid)["output"]
    assert day.gemini.calls == [] and day.claude.calls == [] and _art_objects(sb) == {}
    assert day.state["script"]["art"] is None
    if reason is None:
        assert "art" not in out                                               # 0 = no art: today's output
        return
    assert (out["art"]["status"], out["art"]["reason"]) == ("failed", reason)
    assert out["art"]["usage"]["estimated_micros"] == 0
    if case == "no_capability":
        assert any("did not declare 'post_art'" in r.getMessage() and "'drop2a-test'" in r.getMessage()
                   for r in caplog.records)


@pytest.mark.asyncio
async def test_post_art_a_month_that_still_fits_its_reserve_makes_art(art_world, monkeypatch):
    """The cap reads the run's ET calendar month and nothing else. C-fix (review): the out-of-month rows used to
    carry 0 micros, so a window widened into October (or a rolling 30/60 days) passed — each now carries a whole
    month's cap, and the read's window is pinned too."""
    sb, runs = art_world
    _seed_month_spend(sb, 30_000_000 - pa.run_worst_micros())                 # exactly the reserve left
    _seed_month_spend(sb, 30_000_000, day="2026-10-31")                       # the month before: not counted
    _seed_month_spend(sb, 30_000_000, day="2026-12-01")                       # the month after: not counted
    windows: List[tuple] = []
    real = runs.art_spend_between

    async def spy(start, end):
        windows.append((start, end))
        return await real(start, end)

    monkeypatch.setattr(runs, "art_spend_between", spy)
    day = await _art_day(sb, runs, monkeypatch)
    assert day.day.month == 11 and day.day.year == 2026
    assert windows == [(date(2026, 11, 1), date(2026, 11, 30))]
    assert _script(sb, day.rid)["output"]["art"]["status"] == "ok"


@pytest.mark.parametrize("day, window", [
    (date(2026, 2, 27), (date(2026, 2, 1), date(2026, 2, 28))),
    (date(2028, 2, 29), (date(2028, 2, 1), date(2028, 2, 29))),
    (date(2026, 12, 31), (date(2026, 12, 1), date(2026, 12, 31))),
    (date(2027, 1, 1), (date(2027, 1, 1), date(2027, 1, 31))),
])
@pytest.mark.asyncio
async def test_post_art_the_cap_reads_exactly_the_runs_calendar_month(art_world, monkeypatch, day, window):
    """The month's edges (February, a leap year, the year's turn), through the real `_attach_art`."""
    sb, runs = art_world
    windows: List[tuple] = []

    async def spy(start, end):
        windows.append((start, end))
        return {"micros": 0, "images": 0, "images_uncertain": 0, "runs": 0}

    made: List[dict] = []

    async def make(**kw):
        made.append(kw)
        return pa.gate_record("check_failed", run_id=kw["run_id"], ref=kw["ref"], series=kw["series"])

    monkeypatch.setattr(runs, "art_spend_between", spy)
    monkeypatch.setattr(ss.post_art, "make_post_art", make)
    monkeypatch.setattr(ss, "_art_day_surfaces", lambda output, subject: ("cover",))
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=FakeNews({}))
    holder = {"holder": ({"id": "r", "metadata": {mrs.RUN_WORKER_CAPABILITIES_KEY: ART_CAPS}}, None)}
    got = await svc._attach_art({"id": "r"}, day, "money_map", spot_output(), None,
                                "news:money_map:x", rec=None, news=None, holder=holder, started=ss._mono())
    assert windows == [window] and len(made) == 1 and got["art"]["reason"] == "check_failed"


# ── the art's time: never past the template day's ceiling (C-fix, review) ─────────────────────────


class _StepClock:
    """`ss._mono` held still; the test moves it where the build and the art step's reads take time."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


async def _timed_art_day(sb, runs, monkeypatch, *, build_s: float, read_s: float, real_sleep: float = 0.0):
    """One Money Map art day whose build takes `build_s` and whose two art reads (adopt-first + the month's
    spend) take `read_s` together, on `_StepClock`. Returns the day, the clock and `make_post_art`'s
    (clock at its call, budget_seconds) — or None when it was never called."""
    clock = _StepClock()
    monkeypatch.setattr(ss, "_mono", clock)
    rec = costco()

    async def slow_build():
        clock.t += build_s
        return cands("money_map", rec)

    real_spend = runs.art_spend_between

    async def slow_spend(start, end):
        clock.t += read_s
        if real_sleep:
            await asyncio.sleep(real_sleep)
        return await real_spend(start, end)

    monkeypatch.setattr(runs, "art_spend_between", slow_spend)
    seen: Dict[str, Any] = {}
    real_make = ss.post_art.make_post_art

    async def spy(**kw):
        seen["at"], seen["budget"] = clock(), kw["budget_seconds"]
        return await real_make(**kw)

    monkeypatch.setattr(ss.post_art, "make_post_art", spy)
    day = await _art_day(sb, runs, monkeypatch, news=FakeNews({"money_map": slow_build}))
    return day, clock, (seen or None)


@pytest.mark.parametrize("build_s, read_s", [(150.0, 20.0), (100.0, 5.0), (0.0, 30.0), (200.0, 24.0)])
@pytest.mark.asyncio
async def test_post_art_the_art_never_runs_past_the_template_days_ceiling(art_world, monkeypatch, build_s, read_s):
    """The art's budget is measured AFTER its two ledger reads: a build that used `build_s` and reads that took
    `read_s` leave the art at most what is left of TEMPLATE_DAY_BUDGET_SECONDS, so it ends by the ceiling the worker's
    render pin (280 + 40 + 630 ≤ 960) relies on. Before the fix the budget was taken before the reads (150 s + 20 s
    of reads → the art ended at 300 s)."""
    sb, runs = art_world
    day, clock, seen = await _timed_art_day(sb, runs, monkeypatch, build_s=build_s, read_s=read_s)
    started = 1000.0
    assert seen is not None and day.state["status"] == "accepted"
    assert seen["at"] == pytest.approx(started + build_s + read_s)
    assert seen["at"] + seen["budget"] <= started + ss.TEMPLATE_DAY_BUDGET_SECONDS + 1e-9
    assert seen["budget"] == pytest.approx(min(180.0, ss.TEMPLATE_DAY_BUDGET_SECONDS - build_s - read_s))
    assert _script(sb, day.rid)["output"]["art"]["status"] == "ok"


@pytest.mark.parametrize("build_s, read_s", [(0.0, 240.0), (150.0, 76.0)])
@pytest.mark.asyncio
async def test_post_art_reads_that_eat_the_art_start_window_mean_no_art(art_world, monkeypatch, caplog, build_s,
                                                                       read_s):
    """Reads that leave under ART_MIN_START_SECONDS: no art (`timeout`), no model call, nothing stored — and the day
    still posts."""
    sb, runs = art_world
    with caplog.at_level(logging.INFO):
        day, _clock, seen = await _timed_art_day(sb, runs, monkeypatch, build_s=build_s, read_s=read_s)
    assert seen is None and day.claude.calls == [] and day.gemini.calls == [] and _art_objects(sb) == {}
    art = _script(sb, day.rid)["output"]["art"]
    assert day.state["status"] == "accepted" and (art["status"], art["reason"]) == ("failed", "timeout")
    assert art["usage"]["estimated_micros"] == 0


@pytest.mark.asyncio
async def test_post_art_a_hung_ledger_read_is_cut_at_the_art_start_window(art_world, monkeypatch, caplog):
    """A read that never answers in time is not waited for: the reads are bounded by the art's start window
    (TEMPLATE_DAY_BUDGET_SECONDS − ART_MIN_START_SECONDS from the build's start), so a hung Supabase read costs at
    most that — `timeout`, WARNING, no art, the day posting."""
    sb, runs = art_world
    monkeypatch.setattr(ss, "TEMPLATE_DAY_BUDGET_SECONDS", ss.ART_MIN_START_SECONDS + 0.3)   # a 0.3 s read window
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        day, _clock, seen = await _timed_art_day(sb, runs, monkeypatch, build_s=0.0, read_s=0.0, real_sleep=10.0)
    assert time.monotonic() - t0 < 6.0                                        # never the 10 s read
    assert seen is None and day.claude.calls == [] and day.gemini.calls == []
    art = _script(sb, day.rid)["output"]["art"]
    assert day.state["status"] == "accepted" and art["reason"] == "timeout"
    assert any("art spend" in r.getMessage() and "start window" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_post_art_an_unpriced_claude_model_is_one_error_across_builds(art_world, monkeypatch, caplog):
    """Decision 9: a blank or unpriced MARKETING_ART_CLAUDE_MODEL is no art with ONE ERROR (per value, per process) —
    not an ERROR (a Sentry event) on every eligible build of both lanes. Shares preflight_problem's key."""
    sb, runs = art_world
    monkeypatch.setattr(mrs.settings, "MARKETING_ART_CLAUDE_MODEL", "claude-unknown-9")
    reasons = []

    def errors():
        return [r.getMessage() for r in caplog.records
                if r.levelno >= logging.ERROR and "MARKETING_ART_CLAUDE_MODEL" in r.getMessage()]

    with caplog.at_level(logging.ERROR):
        for _ in range(3):
            sb.tables[mrs.SCRIPTS].rows.clear()
            sb.tables[mrs.RUNS].rows.clear()
            day = await _art_day(sb, runs, monkeypatch)
            assert day.claude.calls == [] and day.gemini.calls == [] and day.state["status"] == "accepted"
            reasons.append(_script(sb, day.rid)["output"]["art"]["reason"])
        assert len(errors()) == 1, errors()                                   # the builds: ONE ERROR, not none
        assert pa.preflight_problem() == "claude_unpriced"                    # the same key: still one ERROR
    assert reasons == ["claude_unpriced"] * 3
    assert len(errors()) == 1, errors()


@pytest.mark.asyncio
async def test_post_art_a_failed_art_still_inserts_the_row_and_the_day_continues(art_world, monkeypatch):
    sb, runs = art_world
    claude = FakeClaude(briefs=[ac.ClaudeUnavailableException("529 overloaded")])
    day = await _art_day(sb, runs, monkeypatch, claude=claude)
    out = _script(sb, day.rid)["output"]
    assert day.state["status"] == "accepted" and day.state["template_id"] == "money_map"
    assert (out["art"]["status"], out["art"]["reason"]) == ("failed", "claude_unavailable")
    assert day.gemini.calls == [] and day.state["script"]["art"] is None and _art_objects(sb) == {}
    assert not lp.breaker_open()                                              # an overload never opens it


@pytest.mark.asyncio
async def test_post_art_two_failed_checks_store_nothing_and_still_post(art_world, monkeypatch):
    sb, runs = art_world
    day = await _art_day(sb, runs, monkeypatch, claude=FakeClaude(checks=[FAIL_TEXT, FAIL_TEXT]))
    art = _script(sb, day.rid)["output"]["art"]
    assert (art["status"], art["reason"], art["attempts"]) == ("failed", "check_failed", 2)
    assert art["usage"]["images"] == 2 and _art_objects(sb) == {}                # a failed picture never stored
    assert day.state["status"] == "accepted" and day.state["script"]["art"] is None


@pytest.mark.asyncio
async def test_post_art_a_contract_breach_is_recorded_with_an_unknown_spend(art_world, monkeypatch, caplog):
    """`make_post_art` never raises; if it ever did, the spend is unknown — recorded so the month's cap reads
    unreadable (fail closed), the day still posting."""
    sb, runs = art_world

    async def boom(**_kw):
        raise RuntimeError("a bug")

    monkeypatch.setattr(ss.post_art, "make_post_art", boom)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        day = await _art_day(sb, runs, monkeypatch)
    art = _script(sb, day.rid)["output"]["art"]
    assert day.state["status"] == "accepted" and art["reason"] == "internal_error"
    assert art["usage"]["estimated_micros"] is None
    assert mrs.art_spend_of(await runs.art_usage_between(date(2026, 11, 1), date(2026, 11, 30))) is None
    assert any("make_post_art RAISED" in r.getMessage() and r.exc_info for r in caplog.records)


def test_post_art_a_record_that_is_not_json_safe_never_vanishes(caplog):
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        out = ss.MarketingScriptService._with_art({"series": "money_map"}, {"usage": {"x": float("nan")}}, "r1")
    assert out["art"]["status"] == "failed" and out["art"]["usage"]["estimated_micros"] is None
    assert any("not JSON-safe" in r.getMessage() for r in caplog.records)


# ── the build: adopt first, the kick waits, the memo ──────────────────────────


@pytest.mark.asyncio
async def test_post_art_a_concurrent_row_is_adopted_with_no_spend(art_world, monkeypatch, caplog):
    """A deploy overlap: another builder's row lands while this build walks its chain — the art step reads the row
    again before spending (adopt-first) and the INSERT adopts it: no model call, no picture."""
    sb, runs = art_world
    day = await _art_day(sb, runs, monkeypatch, kick=False)
    rec = day.rec

    async def concurrent():
        sb.tables[mrs.SCRIPTS].rows.append({
            "run_id": day.rid, "run_date": day.day.isoformat(), "status": "accepted", "source_ref": "concurrent",
            "template_id": "money_map", "generation_id": "other", "tokens_used": 0,
            "output": {"posts": {}, "hook": "h", "video_script": [], "cards": [], "carousel_slides": [],
                       "disclaimer_card": "d"}})
        return cands("money_map", rec)

    day.news.answers["money_map"] = concurrent
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        state = await day.svc.kick(day.rid, claim=_claim(sb, day.rid))
    assert state["status"] == "accepted" and state["source_ref"] == "concurrent"
    assert day.gemini.calls == [] and day.claude.calls == [] and _art_objects(sb) == {}
    assert any("no art spent; the build adopts that row" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_post_art_a_lost_insert_after_art_says_its_spend_is_unrecorded(art_world, monkeypatch, caplog):
    sb, runs = art_world
    day = await _art_day(sb, runs, monkeypatch, kick=False)
    real = runs.insert_script

    async def racing(row, *, run=None):
        sb.tables[mrs.SCRIPTS].rows.append({"run_id": row["run_id"], "run_date": row["run_date"], "status": "accepted",
                                            "source_ref": "concurrent", "template_id": "money_map",
                                            "tokens_used": 0, "output": {"posts": {}}})
        return await real(row, run=run)

    monkeypatch.setattr(runs, "insert_script", racing)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await day.svc.kick(day.rid, claim=_claim(sb, day.rid))
    assert len(day.gemini.calls) == 1                                          # the art was made …
    assert any("lost the INSERT" in r.getMessage() and day.rid in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_post_art_the_kick_answers_generating_while_the_art_runs(art_world, monkeypatch):
    sb, runs = art_world
    monkeypatch.setattr(ss, "TEMPLATE_KICK_WAIT_SECONDS", 0.05)
    day = await _art_day(sb, runs, monkeypatch, gemini=FakeGemini(delay=0.6), kick=False)
    first = await day.svc.kick(day.rid, claim=_claim(sb, day.rid))
    second = await day.svc.kick(day.rid, claim=_claim(sb, day.rid))
    assert first["status"] == second["status"] == "generating"
    assert len(day.svc._builds) == 1 and _mm_calls(day) == 1                  # the second kick JOINED the build
    await _drain(day.svc)
    done = await day.svc.kick(day.rid, claim=_claim(sb, day.rid))
    assert done["status"] == "accepted" and done["script"]["art"] is not None
    assert len(day.gemini.calls) == 1 and _mm_calls(day) == 1


@pytest.mark.asyncio
async def test_post_art_a_rebuild_in_this_process_reuses_the_record_and_never_pays_twice(art_world, monkeypatch,
                                                                                        caplog):
    sb, runs = art_world
    day = await _art_day(sb, runs, monkeypatch, claude=FakeClaude(checks=[FAIL_TEXT, FAIL_TEXT]))
    first = _script(sb, day.rid)["output"]["art"]
    assert first["reason"] == "check_failed"                                   # a FAILURE is memoised too
    calls = (len(day.gemini.calls), len(day.claude.calls))
    sb.tables[mrs.SCRIPTS].rows[:] = [r for r in sb.tables[mrs.SCRIPTS].rows if r["run_id"] != day.rid]
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        again = await day.svc.kick(day.rid, claim=_claim(sb, day.rid))
        await _drain(day.svc)
    assert again["status"] == "accepted" and _mm_calls(day) == 2              # a real rebuild …
    assert (len(day.gemini.calls), len(day.claude.calls)) == calls            # … that paid nothing
    assert _script(sb, day.rid)["output"]["art"] == first
    assert any("already has this process's art record" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_post_art_the_read_back_stops_sending_art_when_the_switch_turns_off(art_world, monkeypatch):
    sb, runs = art_world
    day = await _art_day(sb, runs, monkeypatch)
    sha = _script(sb, day.rid)["output"]["art"]["sha256"]
    assert (await day.svc.kick(day.rid, claim=_claim(sb, day.rid)))["script"]["art"]["sha256"] == sha
    monkeypatch.setattr(mrs.settings, "MARKETING_ART_ENABLED", False)          # the kill switch, read at call time
    assert (await day.svc.kick(day.rid, claim=_claim(sb, day.rid)))["script"]["art"] is None
    assert _script(sb, day.rid)["output"]["art"]["sha256"] == sha              # the record itself is kept


# ── what the server accepts from the worker, and what the posts say ───────────


@pytest.mark.asyncio
async def test_post_art_the_worker_declares_the_art_and_every_post_notes_it(art_world, monkeypatch):
    sb, runs = art_world
    day = await _art_day(sb, runs, monkeypatch)
    out = _script(sb, day.rid)["output"]
    sha = out["art"]["sha256"]
    run = _run_row(sb, day.rid)
    caps = mrs.run_worker_capabilities(run)
    picture = onscreen.image_strings(out["image_spec"], out["logos"])
    # owner decisions 2026-11-20: the Money Map's art is on its COVER only — the cover declares it with its label
    # (accepted), while its picture declaring the art is a surface the record never allows (refused)
    assert mrs.art_declared_problem(out, sha, [onscreen.ART_LABEL], surface="picture",
                                    template=True) == "art_surface_not_allowed"
    voice = _voice(sb, day.rid)
    await runs._check_onscreen_text(run, {"onscreen_text": _video_strings(out) + [onscreen.ART_LABEL],
                                          "voice_asset_id": voice, "opening_art_sha256": sha})
    with pytest.raises(mrs.MarketingRequestInvalid):
        await runs._check_onscreen_text(run, {"onscreen_text": _video_strings(out) + [onscreen.ART_LABEL],
                                              "voice_asset_id": voice, "opening_art_sha256": "0" * 64})
    video = _ready(sb, day.rid, "video", metadata={"opening_art_sha256": sha})
    card = _ready(sb, day.rid, "card", image_role="post_image")
    run["metadata"].update({"video_asset_id": video, "image_asset_id": card})
    specs = [{"platform": p, "format": f, **({"asset_ids": [video if f == "video" else card]} if f != "text" else {})}
             for p, f in sorted(out["post_formats"].items())]
    posts = await runs.create_posts(day.rid, specs, claim=_claim(sb, day.rid))
    by_format = {p["format"]: p for p in posts}
    assert set(by_format) == {"video", "image", "text"}
    # owner decision 2026-11-15: video always made with AI; the Money Map's picture never draws the art (cover only), so
    # its image post keeps today's flag; text never
    assert {f: p["metadata"]["made_with_ai"] for f, p in by_format.items()} == {
        "video": True, "image": False, "text": False}
    for p in posts:
        note = p["metadata"]["art"]
        assert note == {"status": "ok", "reason": None, "subject": out["art"]["subject"], "treatment": out["art"]["treatment"],
                        "redrawn": False, "flags": [], "surfaces": ["cover"],
                        "drawn": {"picture": False, "cover": True}}


@pytest.mark.asyncio
async def test_post_art_a_day_without_a_picture_post_records_and_notes_the_cover_only(art_world, monkeypatch):
    """Final review (2026-11-19): the record's `surfaces` are the ones the DAY can show (`_art_day_surfaces`), not the
    subject's decision-4 pair (owner decisions 2026-11-20: a Money Map day, the one art series). A day frozen with NO
    image post (MARKETING_IMAGE_POSTS off — the same for an
    empty MARKETING_IMAGE_PLATFORMS or a picture that fell back to text) used to record ["picture", "cover"], so the
    owner's review line read "on: video cover (not on the picture)" — the wording of a worker that SKIPPED the art —
    on a day that has no picture at all."""
    from app.services.marketing import review_service as rsv

    sb, runs = art_world
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_POSTS", False)
    day = await _art_day(sb, runs, monkeypatch)
    out = _script(sb, day.rid)["output"]
    assert day.state["status"] == "accepted" and "image" not in out["post_formats"].values()
    assert "video" in out["post_formats"].values()
    art = out["art"]
    assert art["status"] == "ok" and art["surfaces"] == ["cover"]                  # the day carries no picture
    assert mrs.art_surfaces(out) == ("cover",)
    sha = art["sha256"]
    # a picture declaration on such a day is a surface the record does not allow (no picture is ever rendered)
    label = [onscreen.ART_LABEL]
    assert mrs.art_declared_problem(out, sha, label, surface="picture", template=True) == "art_surface_not_allowed"
    assert mrs.art_declared_problem(out, sha, label, surface="cover", template=True) is None
    run = _run_row(sb, day.rid)
    voice = _voice(sb, day.rid)
    await runs._check_onscreen_text(run, {"onscreen_text": _video_strings(out) + [onscreen.ART_LABEL],
                                          "voice_asset_id": voice, "opening_art_sha256": sha})
    video = _ready(sb, day.rid, "video", metadata={"opening_art_sha256": sha})
    run["metadata"].update({"video_asset_id": video})
    specs = [{"platform": p, "format": f, **({"asset_ids": [video]} if f == "video" else {})}
             for p, f in sorted(out["post_formats"].items())]
    posts = await runs.create_posts(day.rid, specs, claim=_claim(sb, day.rid))
    assert {p["format"] for p in posts} == {"video", "text"}
    for p in posts:
        assert p["metadata"]["art"]["surfaces"] == ["cover"]
        assert p["metadata"]["art"]["drawn"] == {"picture": False, "cover": True}
    line = rsv.art_line(posts)
    assert line.endswith(" · check passed · on: video cover") and "not on the" not in line, line


@pytest.mark.asyncio
async def test_post_art_the_memo_keeps_the_subjects_surfaces_and_each_build_narrows_to_its_own_day(art_world,
                                                                                                  monkeypatch):
    """The narrowing is applied when a build RETURNS the record, never to the memo: a rebuild of the same run in this
    process whose freeze now carries a picture post reuses the record (nothing paid twice). Owner decisions 2026-11-20:
    the Money Map's subject is the COVER alone, so even with its picture post back the record never allows the picture
    (`art_surface_not_allowed`) — no series' picture draws art any more."""
    sb, runs = art_world
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_POSTS", False)
    day = await _art_day(sb, runs, monkeypatch)
    first = _script(sb, day.rid)["output"]["art"]
    assert first["status"] == "ok" and first["surfaces"] == ["cover"]
    assert list(day.svc._art_memo.values())[0]["surfaces"] == ["cover"]
    calls = (len(day.gemini.calls), len(day.claude.calls))
    sb.tables[mrs.SCRIPTS].rows[:] = [r for r in sb.tables[mrs.SCRIPTS].rows if r["run_id"] != day.rid]
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_POSTS", True)
    again = await day.svc.kick(day.rid, claim=_claim(sb, day.rid))
    await _drain(day.svc)
    assert again["status"] == "accepted" and (len(day.gemini.calls), len(day.claude.calls)) == calls
    out = _script(sb, day.rid)["output"]
    assert "image" in out["post_formats"].values()
    assert out["art"]["surfaces"] == ["cover"] and out["art"]["sha256"] == first["sha256"]
    assert mrs.art_declared_problem(out, first["sha256"], [onscreen.ART_LABEL], surface="picture",
                                    template=True) == "art_surface_not_allowed"


@pytest.mark.asyncio
async def test_post_art_without_art_create_posts_reads_and_records_exactly_as_before(tworld, monkeypatch):
    """The switch off (no `art` in the output): no note, today's AI flags, and no extra asset read for a text-only
    request. With a FAILED art record the note is there and the flags are still today's."""
    sb, runs = tworld
    rid = await _template_run(sb, runs, rec=costco(), run_date=MON)
    reads: List[str] = []
    real = runs.list_assets

    async def counting(run_id):
        reads.append(run_id)
        return await real(run_id)

    monkeypatch.setattr(runs, "list_assets", counting)
    out = _script(sb, rid)["output"]
    text = [{"platform": p, "format": "text"} for p, f in sorted(out["post_formats"].items()) if f == "text"]
    posts = await runs.create_posts(rid, text, claim=_claim(sb, rid))
    assert reads == [] and all("art" not in p["metadata"] for p in posts)
    assert {p["metadata"]["made_with_ai"] for p in posts} == {False}
    # a failed record: noted on every post (one asset read, to see what was drawn), flags unchanged
    rid2 = await _template_run(sb, runs, rec=costco(), run_date=TUE, tamper=lambda o: o.update(art=pa.gate_record(
        "budget_cap", run_id="r", ref="x", series="money_map")))
    posts = await runs.create_posts(rid2, text, claim=_claim(sb, rid2))
    assert reads == [rid2]
    assert {p["metadata"]["art"]["reason"] for p in posts} == {"budget_cap"}
    assert {p["metadata"]["made_with_ai"] for p in posts} == {False}


# ── which surfaces a day can show ─────────────────────────────────────────────


def _subject(surfaces) -> "pa.ArtSubject":
    return pa.ArtSubject(symbol="CTSO", name="Contoso", surfaces=tuple(surfaces))


@pytest.mark.parametrize("formats, posts, surfaces, want", [
    ({"tiktok": "video", "bluesky": "image", "x": "text"}, None, ("picture", "cover"), ("picture", "cover")),
    ({"tiktok": "video", "bluesky": "text", "x": "text"}, None, ("picture", "cover"), ("cover",)),
    ({"bluesky": "image", "x": "text"}, None, ("picture", "cover"), ("picture",)),
    ({"bluesky": "image", "x": "text"}, None, ("cover",), ()),                     # main lane: never the picture
    ({"x": "text", "bluesky": "text"}, None, ("picture", "cover"), ()),            # a text-only day
    (None, ["tiktok", "x"], ("cover",), ("cover",)),                               # a failed freeze: video stays
    (None, ["x", "bluesky"], ("picture", "cover"), ()),
    ({"x": "carousel"}, None, ("picture", "cover"), ()),                           # formats that do not read back
])
def test_post_art_the_day_surfaces(formats, posts, surfaces, want):
    output: Dict[str, Any] = {"posts": {p: {"caption": "c"} for p in (posts or formats or {})},
                              # what an image format needs to read back (the alt text and the footer)
                              "image_post": {"title": "A title", "paragraphs": ["One.", "Two."]},
                              "image_footer": "Educational only · Caydex"}
    if formats is not None:
        output["post_formats"] = formats
    assert ss._art_day_surfaces(output, _subject(surfaces)) == want


# ── the timing: the poll, and the render's latest start ───────────────────────


def test_post_art_fits_the_workers_poll_and_never_defers_its_render():
    """Decision 11. The art's own ceiling bounds the worker's script poll; and the template day (build + art) plus the
    kicks that collect it plus the voice stage at its worst must leave the render able to START
    (WORKER_DEADLINE − STAGE_START_MARGINS['rendered']) — `TEMPLATE_DAY_BUDGET_SECONDS` is that shared ceiling."""
    from app.config import Settings
    from marketing import voice

    field = Settings.model_fields["MARKETING_ART_BUDGET_SECONDS"]
    art_le = next(m.le for m in field.metadata if getattr(m, "le", None) is not None)
    art_ge = next(m.ge for m in field.metadata if getattr(m, "ge", None) is not None)
    assert (art_ge, art_le, field.default) == (60, 240, 180)
    poll = _worker_constant("SCRIPT_POLL_BUDGET_SECONDS")
    assert ss.TEMPLATE_BUILD_BUDGET_SECONDS + art_le + 2 * ss.TEMPLATE_KICK_WAIT_SECONDS < poll
    deadline = _worker_constant("WORKER_DEADLINE_SECONDS")
    margin = _worker_constant("STAGE_START_MARGIN_SECONDS")          # == STAGE_START_MARGINS["rendered"] (worker pin)
    assert (deadline, margin) == (1800.0, 14 * 60.0)
    voice_worst = voice.worst_case_seconds(_worker_constant("UPLOAD_TIMEOUT_SECONDS"))
    assert ss.TEMPLATE_DAY_BUDGET_SECONDS + 2 * ss.TEMPLATE_KICK_WAIT_SECONDS + voice_worst <= deadline - margin
    # the art can start after a build that ended early, with at least post_art's happy-path minimums …
    assert ss.ART_MIN_START_SECONDS == sum(pa.STEP_SECONDS[s][0] for s in pa.HAPPY_PATH) <= art_ge
    assert ss.TEMPLATE_BUILD_BUDGET_SECONDS < ss.TEMPLATE_DAY_BUDGET_SECONDS
    # … and the art's own default budget is never the binding one on a build that took under a minute and a half
    assert ss.TEMPLATE_DAY_BUDGET_SECONDS - field.default >= 90


# ── news-v23 fix round (2026-11-19): the read-back's `look`, the chips and an older worker ──────────────────────────


def _sample_composed(name: str) -> Dict[str, Any]:
    import test_marketing_news_templates as NT

    (_n, factory, run_date), = [s for s in NT.SAMPLES if s[0] == name]
    out = news_templates.compose(factory(), run_date=run_date, store_state="live", allow_x_url=False)
    out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None,
                     "width": None, "height": None} for r in news_templates.logo_refs(out)]
    return out


def test_every_read_back_carries_the_look_and_the_schema_keeps_it():
    from app.schemas.marketing import READBACK_LOOK

    for out in (_composed(), _sample_composed("costco_money_map")):
        sent = ss.worker_script(out)
        assert sent["look"] == READBACK_LOOK == "news-v23"
        assert WorkerScript.model_validate(sent).model_dump()["look"] == READBACK_LOOK   # declared: never dropped


@pytest.mark.parametrize("name, path", [("ceo_roundup", "rows"), ("insider_mixed", "rows"),
                                        ("costco_money_map", "bars")])
def test_a_holder_without_look_v23_gets_the_picture_without_the_chips_its_walker_would_refuse(name, path):
    """An older worker's closed walker refuses `rows[].chip` and a bars `header.chip` as unknown keys — a failed render
    (not a skipped day). The web sends them only to a holder that declared look_v23; the stripped picture draws a
    subset of the stored spec's strings, so it still registers."""
    from app.schemas.marketing import WORKER_CAPABILITIES, WORKER_CAPABILITY_LOOK_V23
    from marketing import cards, news_layouts as nl

    out = _sample_composed(name)
    old_caps = [c for c in WORKER_CAPABILITIES if c != WORKER_CAPABILITY_LOOK_V23]

    def chips(spec):
        if path == "rows":
            return [r.get("chip") for s in spec["sections"] for r in s["rows"]]
        return [spec["header"].get("chip")]

    full = ss.worker_script(out, capabilities=WORKER_CAPABILITIES)["image_spec"]
    unknown = ss.worker_script(out)["image_spec"]                      # no holder known: the spec as stored
    stripped = ss.worker_script(out, capabilities=old_caps)["image_spec"]
    assert all(chips(full)) and chips(full) == chips(unknown) == chips(out["image_spec"])
    assert not any(chips(stripped)) and out["image_spec"] == full       # a copy: the stored output is untouched
    from app.services.marketing import template_onscreen as tos

    allowed = tos.image_strings(out["image_spec"], out["logos"])
    assert allowed and set(tos.image_strings(stripped, out["logos"])) < set(allowed)
    # exactly the keys a pre-news-v23 walker accepts (main: rows rows {cells, logo}; a bars header {logo, name})
    if path == "rows":
        assert all(set(r) <= {"cells", "logo"} for s_ in stripped["sections"] for r in s_["rows"])
    else:
        assert set(stripped["header"]) == {"logo", "name"}
    # and the news-v23 worker still draws it (the name in the plate's pill, no chip over a row)
    sent = dict(ss.worker_script(out, capabilities=old_caps), image_footer=out["image_footer"])   # formats unfrozen
    image = nl.template_image_for_script(sent, cards.logo_table(sent, {}))
    layout = nl.layout_template(image, font_path=_WORKER_FONT, layout_engine="basic")
    assert not [ln for ln in layout.lines if ln.field.endswith(".chip") and ln.field != "header.chip"]




# ══ news-v23 (2026-11-19): a refused company leaves the post, never the post — through the real build ═════════════


@pytest.mark.asyncio
async def test_news_v23_the_build_drops_a_company_its_template_refuses_and_keys_the_rest(tworld, monkeypatch):
    """The adapter's record reaches the build with one company the TEMPLATE refuses though its record is valid (an EPS
    actual ten times the estimate: a dropped or added digit, `implausible_figures`): `_compose_candidate` drops it
    (`drop_refused_rows`) BEFORE the fact sheet and the ledger key, so the accepted post, its fact sheet and its key name
    exactly the companies it draws. (A Congress count's per-company template gates mirror its record's own — a count of
    one member or an unusable name never builds a record — so there the drop is a backstop only.) Mutation-checked by
    hand (2026-11-20): without the drop call the build refuses the whole record and the day falls through."""
    import test_marketing_news_templates as NT

    sb, runs = tworld
    rec, day = NT.earnings(lead=NT.earnings_row(eps_actual=45.0, eps_estimate=4.5), others=2), NT.EARNINGS_RUN
    kept = R.EarningsDay(series="earnings", reports=rec.reports[1:])
    dropped = rec.reports[0].company
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    _today(monkeypatch, day)
    svc = _svc(runs, FakeNews({"earnings": cands("earnings", rec)}))
    rid = _run(sb, day, caps=_v23_caps("earnings"))
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == "earnings"
    row = _script(sb, rid)
    assert row["source_ref"] == R.ledger_key(kept) and dropped.symbol not in row["source_ref"]
    assert [r["company"]["symbol"] for r in row["fact_sheet"]["record"]["reports"]] == [
        r.company.symbol for r in kept.reports]
    assert dropped.symbol not in {r["key"] for r in row["output"]["logo_refs"]}
    assert dropped.name not in json.dumps(row["output"]["image_spec"])
    assert news_templates.revalidate(row["output"], fact_sheet=row["fact_sheet"], run_date=day) == []


# ══ news-v23 phase 3 (2026-11-20): the weekly theme post through the REAL build ══════════════════════════════════════


def _theme_ledger(sb: FakeSB, day: date) -> List[str]:
    """Twelve weekly theme scripts 7..84 days before `day` (old keys and new), one 85 days back, other series and a
    key-less row — what `runs.theme_refs_between` must hand the adapter: the twelve, oldest first."""
    from app.services.marketing import company_news_rules as R

    want = []
    for k in range(12, 0, -1):
        d = day - timedelta(days=7 * k)
        ref = (f"news:theme_explainer:old-{k}:{d - timedelta(days=2)}" if k % 3 == 0
               else f"news:theme_explainer:t-{k}:{R.THEME_ANGLES[k % 2]}:{d}")
        sb.tables[mrs.SCRIPTS].rows.append({"run_id": str(uuid.uuid4()), "run_date": d.isoformat(), "status": "accepted",
                                            "template_id": "theme_explainer", "source_ref": ref, "output": {}})
        want.append(ref)
    far = day - timedelta(days=R.THEME_HISTORY_DAYS + 1)
    for row in ({"run_date": far.isoformat(), "template_id": "theme_explainer",
                 "source_ref": f"news:theme_explainer:far:biggest:{far}"},
                {"run_date": (day - timedelta(days=1)).isoformat(), "template_id": "money_map",
                 "source_ref": "news:money_map:COST:FY2025"},
                {"run_date": (day - timedelta(days=14)).isoformat(), "template_id": "theme_explainer",
                 "source_ref": None}):
        sb.tables[mrs.SCRIPTS].rows.append({"run_id": str(uuid.uuid4()), "status": "accepted", "output": {}, **row})
    return want


@pytest.mark.parametrize("angles_cap", [True, False])
@pytest.mark.asyncio
async def test_phase3_the_theme_build_hands_the_adapter_its_rotation_window_and_the_holders_angles(
        tworld, monkeypatch, caplog, angles_cap):
    """Wednesday: the build reads the theme rotation's OWN window (THEME_HISTORY_DAYS back — twelve weekly keys,
    nothing older, no other series) and hands it to the theme series ONLY, with the angles the holder can draw: every
    angle with `theme_angles`, else "make_money" alone (WARNING naming the run and the capability)."""
    from app.services.marketing import company_news_rules as R

    sb, runs = tworld
    rec, day, _klass, _layout = _2b_case("theme_explainer")
    want = _theme_ledger(sb, day)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    _today(monkeypatch, day)
    news = FakeNews({"theme_explainer": cands("theme_explainer", rec)})
    svc = _svc(runs, news)
    rid = _run(sb, day, caps=_v23_caps("theme_explainer") if angles_cap else DROP2_CAPS)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_claim(sb, rid))
        await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == "theme_explainer"
    (call,) = news.calls
    assert call["series"] == "theme_explainer"
    assert list(call["kw"]["theme_history"]) == want
    assert call["kw"]["theme_angles"] == (R.THEME_ANGLES if angles_cap else ("make_money",))
    warned = [r.getMessage() for r in caplog.records if "did not declare 'theme_angles'" in r.getMessage()]
    assert (len(warned) == 1 and rid in warned[0]) if not angles_cap else warned == []


@pytest.mark.asyncio
async def test_phase3_another_days_build_never_reads_the_theme_window(tworld, monkeypatch):
    """No other day pays for the rotation's read, and no other series is handed its inputs (a Tuesday: owner decisions
    2026-11-20 put the theme on Monday's chain, after the Form 4 pair)."""
    sb, runs = tworld
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    _today(monkeypatch, TUE)
    reads = []
    real = runs.theme_refs_between

    async def spy(*a, **kw):
        reads.append(a)
        return await real(*a, **kw)

    monkeypatch.setattr(runs, "theme_refs_between", spy)
    news = FakeNews({})
    svc = _svc(runs, news)
    rid = _run(sb, TUE)
    await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert reads == [] and news.calls and all(c["kw"] == {} for c in news.calls)
    assert "theme_explainer" not in [c["series"] for c in news.calls]


@pytest.mark.asyncio
async def test_phase3_a_failed_theme_window_read_stops_the_build_never_a_guess(tworld, monkeypatch, caplog):
    """The rotation never picks a theme on a guess about its last post: a failed read RAISES out of the build (ERROR
    with the run id), no script is written, the adapter is never asked — and the next kick builds the day afresh."""
    sb, runs = tworld
    rec, day, _klass, _layout = _2b_case("theme_explainer")
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    _today(monkeypatch, day)
    real = runs.theme_refs_between
    fail = {"n": 1}

    async def flaky(*a, **kw):
        if fail["n"]:
            fail["n"] -= 1
            raise mrs.MarketingRunError("theme_refs_between failed: 520")
        return await real(*a, **kw)

    monkeypatch.setattr(runs, "theme_refs_between", flaky)
    news = FakeNews({"theme_explainer": cands("theme_explainer", rec)})
    svc = _svc(runs, news)
    rid = _run(sb, day, caps=_v23_caps("theme_explainer"))
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        with pytest.raises(mrs.MarketingRunError):
            await svc.kick(rid, claim=_claim(sb, rid))
    assert news.calls == [] and not [r for r in sb.tables[mrs.SCRIPTS].rows if r.get("run_id") == rid]
    assert any("theme rotation's ledger read failed" in r.getMessage() and rid in r.getMessage()
               for r in caplog.records)
    for _ in range(50):
        if not svc._builds:
            break
        await asyncio.sleep(0)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == "theme_explainer"


@pytest.mark.parametrize("angles_cap", [True, False])
@pytest.mark.asyncio
async def test_phase3_the_read_back_strips_the_theme_marks_for_a_holder_without_theme_angles(tworld, monkeypatch,
                                                                                            caplog, angles_cap):
    """A holder that did not declare `theme_angles` is handed the grid without its tiles' chips, its icon and its
    sub-line break, and the opening card without its list cover — a subset of the stored strings (the server's
    allow-list), so the picture still registers; a `theme_angles` holder gets the stored spec and cover as they are."""
    sb, runs = tworld
    rec, day, _klass, _layout = _2b_case("theme_explainer")
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    _today(monkeypatch, day)
    svc = _svc(runs, FakeNews({"theme_explainer": cands("theme_explainer", rec)}))
    rid = _run(sb, day, caps=_v23_caps("theme_explainer") if angles_cap else DROP2_CAPS)
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    out = _script(sb, rid)["output"]
    assert out["image_spec"].get("icon") == "chip" and out["image_spec"].get("sub_break")
    assert all("chip" in t for t in out["image_spec"]["tiles"]) and out["opening_card"].get("list")
    script = state["script"]
    if angles_cap:
        assert script["image_spec"] == out["image_spec"] and script["opening_card"] == out["opening_card"]
        return
    spec, card = script["image_spec"], script["opening_card"]
    assert "icon" not in spec and "sub_break" not in spec and not any("chip" in t for t in spec["tiles"])
    assert [t["name"] for t in spec["tiles"]] == [t["name"] for t in out["image_spec"]["tiles"]]
    assert "list" not in card and {k: v for k, v in out["opening_card"].items() if k != "list"} == card
    allowed = set(onscreen.image_strings(out["image_spec"], out["logos"]))
    assert set(onscreen.image_strings(spec, out["logos"])) <= allowed
    assert onscreen.validate_image_spec(spec, onscreen.logo_keys(out["logos"]), footer=out["image_footer"]) is None


@pytest.mark.asyncio
async def test_phase3_a_ranked_picture_built_before_a_reclaim_freezes_as_text_for_a_holder_without_theme_angles(
        tworld, caplog):
    """The market-value angle is offered only to a `theme_angles` holder; if an older image re-claims the run while it
    is being written, the freeze reads the holder ONCE at write time and freezes the posts as TEXT (WARNING naming
    `theme_angles` and the theme-angles worker) — never a `ranked` picture handed to a worker that refuses it."""
    import test_marketing_news_templates as NT

    sb, runs = tworld
    svc = _svc(runs, FakeNews({}))
    out = _composed(NT.power_machine(), NT.THEME_WED)
    assert out["image_spec"]["layout"] == "ranked"
    for caps, pictured in (([*DROP2_CAPS, "look_v23", "theme_angles"], True), ([*DROP2_CAPS, "look_v23"], False)):
        rid = _run(sb, NT.THEME_WED, caps=caps)
        with caplog.at_level(logging.WARNING, logger=ss.logger.name):
            frozen = await svc._frozen_output(out, NT.THEME_WED, run_id=rid, gen_id="g", image_posts=True)
        assert ("image" in frozen["post_formats"].values()) is pictured, caps
        if not pictured:
            assert frozen["image_fallback"]["reason"] == "worker_capability"
            assert any("'theme_angles'" in r.getMessage() and "theme-angles" in r.getMessage() and rid in r.getMessage()
                       for r in caplog.records)


# ══ news-v23 phase 3, the theme's last three angles (2026-11-20): the class-C angles' switch, the holder's layouts ════

@pytest.mark.parametrize("classes, want", [("A,C,F", ("A", "C", "F")), ("A,F", ("A", "F"))])
@pytest.mark.asyncio
async def test_phase3_the_theme_build_hands_the_adapter_the_classes_switched_on(tworld, monkeypatch, classes, want):
    """The theme series is told which content classes are on NOW (`theme_classes`), so the insider scorecard and the
    Congress angle (class C facts under the class-F series) are skipped with no read while class C is off."""
    sb, runs = tworld
    rec, day, _klass, _layout = _2b_case("theme_explainer")
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", classes)
    _today(monkeypatch, day)
    news = FakeNews({"theme_explainer": cands("theme_explainer", rec)})
    svc = _svc(runs, news)
    rid = _run(sb, day, caps=_v23_caps("theme_explainer"))
    await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    (call,) = [c for c in news.calls if c["series"] == "theme_explainer"]
    assert call["kw"]["theme_classes"] == want


@pytest.mark.parametrize("angle", ["growth_profit", "insiders", "congress"])
@pytest.mark.asyncio
async def test_phase3_a_theme_post_of_filings_needs_class_c_on_at_create_posts(tworld, monkeypatch, angle):
    """Each table angle builds its Wednesday, draws its own layout and re-checks clean; at create_posts the CLASS gate
    reads the record's facts (`company_news_rules.record_content_classes`): the insider scorecard and the Congress angle
    state class-C filings under the class-F series, so with class C switched off after the build their posts are
    refused (409 → `template_refused`, nothing recorded) — growth + profit (class F alone) is not. Mutation-checked by
    hand (2026-11-20): with the record-class check removed from create_posts, the class-C cases recorded their posts."""
    import test_marketing_news_templates as NT

    sb, runs = tworld
    rec = {"growth_profit": NT.silicon_growth, "insiders": NT.cyber_insiders, "congress": NT.battlefield_congress}[angle]()
    day = NT.THEME_WED
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", EVERY_SERIES)
    _today(monkeypatch, day)
    news = FakeNews({"theme_explainer": cands("theme_explainer", rec)})
    svc = _svc(runs, news)
    rid = _run(sb, day, caps=_v23_caps("theme_explainer"))
    state = await svc.kick(rid, claim=_claim(sb, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == "theme_explainer"
    row = _script(sb, rid)
    out = row["output"]
    assert out["image_spec"]["layout"] == news_templates.THEME_LAYOUT_OF_ANGLE[angle]
    assert out["opening_card"]["list"]["layout"] == out["image_spec"]["layout"]
    assert news_templates.revalidate(out, fact_sheet=row["fact_sheet"], run_date=day) == []
    video, card = _ready(sb, rid, "video"), _ready(sb, rid, "card", image_role="post_image")
    _run_row(sb, rid)["metadata"].update({"video_asset_id": video, "image_asset_id": card})
    specs = [{"platform": p, "format": f, **({"asset_ids": [video if f == "video" else card]} if f != "text" else {})}
             for p, f in sorted(out["post_formats"].items())]
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,F")
    if angle == "growth_profit":
        assert await runs.create_posts(rid, specs, claim=_claim(sb, rid))
        return
    with pytest.raises(mrs.MarketingTemplateRefused, match="class"):
        await runs.create_posts(rid, specs, claim=_claim(sb, rid))
    assert sb.tables[mrs.POSTS].rows == []
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    posts = await runs.create_posts(rid, specs, claim=_claim(sb, rid))
    assert {p["status"] for p in posts} == {"pending_review"}


@pytest.mark.parametrize("angle", ["growth_profit", "insiders", "congress"])
@pytest.mark.asyncio
async def test_phase3_a_table_picture_built_before_a_reclaim_freezes_as_text_for_a_holder_without_theme_angles(
        tworld, caplog, angle):
    """The table angles draw under `theme_angles` too (`schemas.WORKER_LAYOUTS_THEME`): a picture of one built before an
    older image re-claimed the run is frozen as TEXT, never handed to a worker that refuses it; the read-back strips the
    theme cover's list for such a holder."""
    import test_marketing_news_templates as NT

    sb, runs = tworld
    svc = _svc(runs, FakeNews({}))
    rec = {"growth_profit": NT.silicon_growth, "insiders": NT.cyber_insiders, "congress": NT.battlefield_congress}[angle]()
    out = _composed(rec, NT.THEME_WED)
    from app.schemas.marketing import WORKER_LAYOUTS_THEME

    assert out["image_spec"]["layout"] in WORKER_LAYOUTS_THEME
    for caps, pictured in (([*DROP2_CAPS, "look_v23", "theme_angles"], True), ([*DROP2_CAPS, "look_v23"], False)):
        rid = _run(sb, NT.THEME_WED, caps=caps)
        with caplog.at_level(logging.WARNING, logger=ss.logger.name):
            frozen = await svc._frozen_output(out, NT.THEME_WED, run_id=rid, gen_id="g", image_posts=True)
        assert ("image" in frozen["post_formats"].values()) is pictured, caps
    sent = ss.worker_script(out, capabilities=frozenset(DROP2_CAPS + ["look_v23"]), run_id="r1")
    assert "list" not in sent["opening_card"]


# ── review of 2026-11-26: the alt text describes the picture the worker REGISTERED ──────────────────────────────────

_PRE_V23_CAPS = ["news_templates", "post_image", "layouts_2b"]
_ALL_CAPS = [*_PRE_V23_CAPS, "video_v2", "look_v23"]


def _stored(factory, day):
    import test_marketing_news_templates as NT

    from app.schemas.marketing import IMAGE_PLATFORMS

    run_date = getattr(NT, day)
    out = news_templates.compose(getattr(NT, factory)(), run_date=run_date, store_state="live", allow_x_url=False)
    out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in out["logo_refs"]]
    out = ss.freeze_post_formats(out, run_date, image_posts=True, x_images=False, image_platforms=IMAGE_PLATFORMS)
    assert "image" in out["post_formats"].values()
    return json.loads(json.dumps(out))


def _drawn_by(out, caps):
    """The strings the REAL worker declares for its post image under `caps` (the worker's own read-back)."""
    from marketing import cards
    from marketing import news_layouts as nl

    sent = json.loads(WorkerScript(**ss.worker_script(out, run_id="r", capabilities=caps)).model_dump_json())
    image = nl.template_image_for_script(sent, cards.logo_table(sent, {}))
    nl.layout_template(image, font_path=_WORKER_FONT, layout_engine="basic")      # it lays out (never refused)
    return list(image.strings)


def NT_moves(factory):
    import test_marketing_news_templates as NT

    return getattr(NT, factory)().moves


def _figures(text):
    """Every dollar figure in `text` ("$4.6B", "$66.38"), its sentence punctuation left off."""
    return set(re.findall(r"\$[0-9](?:[0-9,.]*[0-9])?[KMBT]?", text))


@pytest.mark.parametrize("factory, day", [("rivian", "THU"), ("coherent", "THU"), ("f13s_list8", "SPOT_SAT")])
def test_review_1126_the_alt_text_describes_the_picture_the_worker_drew(factory, day):
    """Review of 2026-11-26 (compliance/accessibility): a loss-year Money Map drawn by a worker WITHOUT look_v23 (a
    rolled-back worker, or an older one re-claiming the built run) shows the flow steps and their callout — but its alt
    text described the costs card ("costs and expenses $9.7B … costs were $166.38"), bars that are not in the image; a
    13F list of eight drawn by such a worker shows five rows and no "+N more" line while its alt text said six rows and
    "2 more changes are listed in the caption". The output now carries that picture's alt text too
    (`image_post_pre_v23`) and the publisher sends the one that describes the REGISTERED picture
    (`news_templates.picture_alt`, from its declared strings). Mutation-checked: `picture_alt` returning `image_post`
    always fails the older-worker leg."""
    out = _stored(factory, day)
    pre = out["image_post_pre_v23"]
    assert pre["title"] == out["image_post"]["title"] and pre != out["image_post"]               # anti-vacuity
    full = _drawn_by(out, _ALL_CAPS)
    older = _drawn_by(out, _PRE_V23_CAPS)
    marks = onscreen.v23_only_strings(out["image_spec"], out["logos"])
    assert marks & set(full) and not marks & set(older)
    # each picture registers (the server's allow-list) and gets ITS alt text
    for drawn in (full, older):
        mrs.MarketingRunService._check_template_image_text("r", out, drawn, capabilities=_ALL_CAPS)
    assert news_templates.picture_alt(out, full) == out["image_post"]
    assert news_templates.picture_alt(out, older) == pre
    for drawn, alt in ((full, out["image_post"]), (older, pre)):
        said = " ".join(alt["paragraphs"][:-1])                         # the source line is a date, not a picture figure
        # a promise of rows "in the caption" only beside a picture that draws its "+N more" line
        assert ("listed in the caption" in said) is any(t.startswith("+") and t.endswith("in the caption")
                                                        for t in drawn), (said, drawn)
        if factory == "f13s_list8":
            k = sum(1 for m in NT_moves(factory) if m.company.name in drawn)
            assert said.startswith(f"It lists {k} of the filing's holding changes"), (k, said)
        else:
            # every figure the Money Map alt text states is on its picture; the costs words only beside the costs card
            on_picture = _figures(" ".join(drawn).replace("−", "-"))
            assert _figures(said.replace("−", "-")) <= on_picture, (said, sorted(on_picture))
            assert ("costs and expenses" in said) is ("Costs and expenses" in drawn), (said, drawn)
    # unreadable declarations never guess: the stored alt text
    for junk in (None, "x", [1, 2], {}):
        assert news_templates.picture_alt(out, junk) == out["image_post"]


@pytest.mark.parametrize("factory, day", [("costco", "THU"), ("amazon", "THU"), ("berkshire", "TUE"),
                                          ("ceo_roundup", "MON"), ("isp_cluster", "SPOT_THU")])
def test_review_1126_an_output_whose_older_picture_is_its_own_carries_one_alt_text(factory, day):
    """A profit year, a 13F list of at most five rows, a Form 4 list: an older worker's picture says what the stored alt
    text says (or never reaches one) — no `image_post_pre_v23`, and `picture_alt` is the stored `image_post` whatever
    was drawn."""
    out = _stored(factory, day)
    assert "image_post_pre_v23" not in out
    assert news_templates.picture_alt(out, []) == out["image_post"]


@pytest.mark.asyncio
async def test_review_1126_load_post_image_sends_the_older_pictures_alt_text(monkeypatch):
    """The publisher's one read: `load_post_image` builds the alt text from the variant the REGISTERED picture's declared
    strings select — the older worker's flow-card Rivian gets the flow alt text, the look_v23 one the costs alt text."""
    from app.services.marketing import outlet_base

    out = _stored("rivian", "THU")

    class Ledger:
        def __init__(self, drawn):
            self.drawn = drawn

        async def get_asset(self, _id):
            return {"id": "a1", "run_id": "r1", "kind": "card", "status": "ready", "storage_path": "x/card-1.jpg",
                    "content_type": "image/jpeg", "bytes": 10, "sha256": "a" * 64,
                    "metadata": {"image_role": "post_image", "onscreen_text": self.drawn}}

        async def get_script(self, _rid):
            return {"run_id": "r1", "status": "accepted", "output": out}

        @staticmethod
        def public_url(path):
            return f"https://example.supabase.co/storage/v1/object/public/marketing-media/{path}"

    row = {"id": "p1", "run_id": "r1", "platform": "bluesky", "format": "image", "asset_ids": ["a1"]}
    for caps, want in ((_ALL_CAPS, out["image_post"]), (_PRE_V23_CAPS, out["image_post_pre_v23"])):
        ledger = Ledger(_drawn_by(out, caps))
        monkeypatch.setattr(mrs, "get_marketing_run_service", lambda: ledger)
        image = await outlet_base.load_post_image(row)
        assert image.title == want["title"] and list(image.paragraphs) == want["paragraphs"], caps
