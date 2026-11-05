"""Publish, expire, unpublish and cache sync of the Wiser weekly pieces
(`app/services/learn_weekly/publisher.py`, plan §1.10).

Hermetic: the REAL ledger and publisher run against the in-memory PostgREST fake of
`test_learn_weekly_ledger.py` (which enforces migration 204's CHECKs, UNIQUE keys and FK), extended here
with `money_move_articles` (its UNIQUE id, slug and case-folded title — the migration-204 index) and a
Storage fake; Telegram is an `httpx.MockTransport`. Every `get_supabase` binding the code reaches is
patched (ledger, publisher, learn_audio_urls, the content service), so nothing can reach production.

What must never regress (each a way a weekly piece goes wrong in front of users):
  * a DRY RUN piece never writes `money_move_articles` (it ends `rehearsed`);
  * the live row is ours: id = uuid5(NS_WEEKLY, slug), source 'weekly', `content.isFeatured` equal to the
    column, the Deep Dive's negative sort_order, the audio in PUBLIC form; a replay writes nothing new;
  * a slug or title another article holds ends the piece `expired` with an alert — never a half publish;
  * `publishing` recovery both ways (row exists → published; none → redo inside the hold, expired after);
  * expiry never touches `publishing`, and cancels open narration jobs;
  * unpublish deletes only the weekly row and only a weekly audio object, and the featured rule then falls
    back to the previous Deep Dive — never to a NEVER_FEATURE slug;
  * the cache is invalidated on publish/unpublish, and `cache_sync` follows the ledger's newest stamp.

The shared fakes and builders here are imported by `test_learn_weekly_review_bot.py` and
`test_learn_weekly_alerts.py`.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
import pytest

from app.config import settings
from app.integrations import telegram
from app.services import learn_audio_urls
from app.services import money_moves_content_service as mmcs
from app.services.learn_weekly import ledger, publisher, review, schedule
from app.services.learn_weekly import contracts as c
from app.services.marketing import review_service as rs
from app.services.money_moves_content_service import MoneyMovesContentService
from test_learn_weekly_ledger import FakeSupabase as _LedgerFake
from test_learn_weekly_ledger import _api_error, _Query, _Result, _Table, _wire

UTC = timezone.utc
OWNER = 424242
# Built from two halves, like test_marketing_review_bot.py: a token-shaped literal in the source is
# what GitHub secret scanning flagged on 2026-10-23 (it is Telegram's docs example, not a real bot).
_BOT_ID = "123456789"
_BOT_SECRET = "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawZ"  # 35 chars, the real shape
TOKEN = f"{_BOT_ID}:{_BOT_SECRET}"
SECRET = "s3cret_Webhook-token"
SHA = "a" * 64
SHA2 = "b" * 64
DD = schedule.deep_dive_times_for(date(2026, 10, 23))       # 2026-W43: Fri 23:00 ET publish
KEY = DD.period_key
PUBLISH = DD.publish_at
HOLD = DD.hold_until
SLUG = "how-data-centres-got-expensive-2026-10-23"
TITLE = "How Data Centres Got Expensive"
ARTICLES = publisher.ARTICLES
DECISION_ID = 9001

ARTICLE_COLUMNS = frozenset({
    "id", "title", "subtitle", "category", "author_name", "author_credentials", "author_avatar_name",
    "published_at", "read_time_minutes", "sections", "statistics", "related_articles", "created_at", "slug",
    "content", "view_count", "is_featured", "has_audio_version", "audio_url", "audio_duration_seconds",
    "sort_order", "updated_at", "image_url", "source",
})


# ── fakes ─────────────────────────────────────────────────────────────────────────────────────


class _ArticlesTable(_Table):
    def delete(self):
        return _Query(self, "delete")


class FakeBucket:
    def __init__(self, store: "FakeStorage", bucket: str) -> None:
        self.store, self.bucket = store, bucket

    def remove(self, paths):
        if self.store.fail_remove is not None:
            raise self.store.fail_remove
        self.store.removed.append((self.bucket, list(paths)))
        return [{"name": p} for p in paths]

    def create_signed_urls(self, paths, ttl):
        if self.store.fail_sign is not None:
            raise self.store.fail_sign
        self.store.signed.append((self.bucket, list(paths), ttl))
        return [{"path": p, "signedURL": f"https://x.supabase.co/storage/v1/object/sign/{self.bucket}/{p}"
                                         f"?token=SIGNEDTOKEN123"} for p in paths]


class FakeStorage:
    def __init__(self) -> None:
        self.removed: List[tuple] = []
        self.signed: List[tuple] = []
        self.fail_remove: Optional[Exception] = None
        self.fail_sign: Optional[Exception] = None

    def from_(self, bucket):
        return FakeBucket(self, bucket)


class LearnFake(_LedgerFake):
    """The ledger fake + `money_move_articles` (UNIQUE id, slug, lower(title)) + Storage."""

    def __init__(self) -> None:
        super().__init__()
        self.tables[ARTICLES] = _ArticlesTable(
            self, ARTICLES, ARTICLE_COLUMNS,
            {"is_featured": False, "has_audio_version": False, "sort_order": 0, "source": "bundle"},
            [("id",), ("slug",)], lambda r: [])
        self.storage = FakeStorage()

    def _validate(self, t, row, others):
        super()._validate(t, row, others)
        if t.name == ARTICLES:
            title = str(row.get("title") or "").lower()
            if any(str(o.get("title") or "").lower() == title for o in others):
                raise _api_error("23505", "duplicate key value violates unique constraint "
                                          "idx_money_move_articles_title_lower")
            if row.get("source") not in ("bundle", "weekly"):
                raise _api_error("23514", "new row violates check constraint money_move_articles_source_check")

    def _delete(self, q):
        t = q.t
        matched = [r for r in t.rows if all(f(r) for f in q.filters)]
        t.rows[:] = [r for r in t.rows if r not in matched]
        return _Result([_wire(r) for r in matched])

    def articles(self) -> List[Dict[str, Any]]:
        return self.tables[ARTICLES].rows


class FakeTelegram:
    """Records every Bot API call; `script[method]` is a FIFO of canned answers: (status, body), an
    Exception to raise, or a callable(payload) returning one."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.script: Dict[str, list] = {}
        self._next_id = 500

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        queue = self.script.get(method)
        if queue:
            item = queue.pop(0)
            if callable(item):
                item = item(payload)
            if isinstance(item, BaseException):
                raise item
            if item is not None:
                status, body = item
                return httpx.Response(status, json=body)
        self._next_id += 1
        if method in ("sendMessage", "sendAudio", "editMessageText", "editMessageReplyMarkup"):
            return httpx.Response(200, json={"ok": True, "result": {
                "message_id": payload.get("message_id") or self._next_id,
                "chat": {"id": payload.get("chat_id")}, "text": payload.get("text")}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def of(self, method: str) -> List[Dict[str, Any]]:
        return [p for m, p in self.calls if m == method]

    def methods(self) -> List[str]:
        return [m for m, _ in self.calls]

    def answers(self) -> List[str]:
        return [p.get("text") for p in self.of("answerCallbackQuery")]


def too_many(retry_after: int = 7):
    return (429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                  "parameters": {"retry_after": retry_after}})


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake(monkeypatch):
    f = LearnFake()
    for mod in (ledger, publisher, learn_audio_urls, mmcs):
        monkeypatch.setattr(mod, "get_supabase", lambda f=f: f)
    learn_audio_urls.reset_cache_for_tests()
    yield f
    learn_audio_urls.reset_cache_for_tests()


@pytest.fixture
def tg(monkeypatch):
    t = FakeTelegram()
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(t.handler)))
    return t


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_REVIEW_CHAT_ID", OWNER)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(settings, "MARKETING_PUBLIC_BASE_URL", "https://caydexinvest.com")
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(rs, "SEND_SPACING_SECONDS", 0.0)
    monkeypatch.setattr(rs, "_rate_limited_until", 0.0)
    monkeypatch.setattr(review, "_bundle_retry_at", {})


@pytest.fixture(autouse=True)
def _content_cache_restored(monkeypatch):
    """`invalidate` mutates class state; every test leaves it exactly as it found it."""
    for attr in ("_cache", "_generation", "_built_at", "_inflight", "_inflight_started_at"):
        monkeypatch.setattr(MoneyMovesContentService, attr, getattr(MoneyMovesContentService, attr))


@pytest.fixture
def no_narration_module(monkeypatch):
    """`publisher.audio_public_url` falls back to its own public form while narration.py is absent."""
    monkeypatch.setattr(publisher, "_narration_module", lambda: None)


# ── builders ──────────────────────────────────────────────────────────────────────────────────


def candidate(i: int, *, category: str = "blueprints") -> Dict[str, Any]:
    tickers = [["CRWV", "NVDA", "ORCL"], ["AVGO", "MRVL"], ["VST", "CEG"]][i % 3]
    return {
        "kind": "theme", "key": f"theme-{i}", "title": f"Theme {i}", "score": 3.5 - i, "tickers": tickers,
        "components": {"mean_top3": 3.5 - i}, "reason": "ranked", "label": f"Topic {i + 1} label",
        "subjects": [{"name": f"Company {i} Inc.", "cik": f"000000000{i + 1}", "ticker": tickers[0]}],
        "peg_facts": [{"id": "P1", "text": f"On October 20, 2026, Company {i} disclosed a supply agreement "
                                           f"worth $1.2 billion over five years.",
                       "origin": "sec_peg", "url": f"https://www.sec.gov/Archives/edgar/data/{i + 1}/x-index.htm",
                       "accession": f"000000000{i + 1}-26-000001", "entry_id": None}],
        "category": category,
    }


def topic(*, chosen: Optional[int] = 0, n: int = 3, cards: Optional[List[int]] = None,
          auto: bool = False) -> Dict[str, Any]:
    t = {"version": 1, "candidates": [candidate(i, category=c.CATEGORIES[i % 3]) for i in range(n)],
         "chosen_index": chosen, "owner_pick": None if (chosen is None or auto) else chosen,
         "auto_picked": auto, "demand_context": {"most_added": ["NVDA", "CRWV"]},
         "scored_at": "2026-10-22T22:00:00Z"}
    if cards is not None:
        t["card_message_ids"] = cards
    return t


def draft(*, title: str = TITLE, paragraphs: int = 3, words: int = 40) -> Dict[str, Any]:
    para = " ".join(["capacity"] * words)
    return {
        "slug": SLUG, "title": title, "subtitle": "Why compute got scarce.", "cardSubtitle": "Power, chips, land.",
        "category": "blueprints", "author": dict(c.AUTHOR), "readTimeMinutes": 9, "tagLabel": "CASE STUDY",
        "isFeatured": False, "hasAudioVersion": False, "heroGradientColors": ["EA580C", "C2410C", "9A3412"],
        "keyHighlights": [{"icon": "bolt.fill", "title": "Power", "description": "Grid access is the bottleneck."}],
        "statistics": [{"value": "$1.2 billion", "label": "Supply agreement"}],
        "sections": [{"title": f"Part {s}", "icon": "building.2.fill", "hasGlowEffect": False,
                      "content": [{"type": "paragraph", "text": para} for _ in range(paragraphs)]
                      + [{"type": "bulletList", "items": ["one point", "two point"]},
                         {"type": "callout", "style": "info", "text": "Educational only."}]}
                     for s in range(2)],
        "viewCount": "",
    }


def make_piece(fake: LearnFake, status: str, *, dry_run: bool = False, key: str = KEY, kind: Optional[str] = None,
               sched: datetime = PUBLISH, hold: datetime = HOLD, with_topic: Any = True, slug: Optional[str] = SLUG,
               title: Optional[str] = TITLE, the_draft: Any = None, job: Optional[str] = "ready",
               review_ids: Optional[List[int]] = None, review_round: int = 1,
               **extra: Any) -> Dict[str, Any]:
    """A piece already in `status` (fields written straight into the fake — setup, not the code under
    test), with one narration job of status `job` (None = no job)."""
    kind = kind or (c.KIND_DEEP_DIVE if key.endswith("deep_dive") else c.KIND_SLOT)
    row = ledger.create_piece_sync(key, kind, scheduled_publish_at=sched, hold_until=hold, dry_run=dry_run, sb=fake)
    pid = row["id"]
    job_id = None
    if job is not None:
        j = ledger.enqueue_job_sync(pid, round=1, script=[{"i": 0, "text": "x", "role": "body"}],
                                    script_sha256=SHA, engine="chatterbox", voice="caydex_v2",
                                    pipeline_version="p1", sb=fake)
        job_id = j["id"]
        if job == "ready":
            fake.row(ledger.JOBS, job_id).update({
                "status": "ready", "audio_path": f"audio/weekly/{slug or 'x'}-abcdef012345.m4a",
                "audio_bytes": 8_600_000, "duration_seconds": 692.4, "timings": [],
                "checks": {"blocks": [{"i": 3, "coverage": 0.91}, {"i": 7, "coverage": 0.99}]},
                "finished_at": ledger._iso(sched - timedelta(hours=10)),
            })
        else:
            fake.row(ledger.JOBS, job_id)["status"] = job
    fields: Dict[str, Any] = {
        "status": status, "slug": slug, "title": title,
        "draft": draft() if the_draft is None else the_draft, "draft_sha256": SHA, "category": "blueprints",
        "narration_job_id": job_id, "review_round": review_round,
        "review_message_ids": [DECISION_ID] if review_ids is None else review_ids,
        "tokens_used": 48210, "validation": {"judge": {"mode": "shadow", "flags": [
            {"rule": "judge_directive", "field": "section 2", "quote": "consider buying"}]}},
        "subjects": [{"name": "Company 0 Inc.", "cik": "0000000001", "ticker": "CRWV"}],
        "last_stage": "narration",
    }
    if with_topic is True:
        fields["topic"] = topic()
    elif with_topic is not False:
        fields["topic"] = with_topic
    fields.update(extra)
    fake.row(ledger.PIECES, pid).update(fields)
    return copy.deepcopy(fake.row(ledger.PIECES, pid))


def piece_row(fake: LearnFake, pid: str) -> Dict[str, Any]:
    return fake.row(ledger.PIECES, pid)


def bundle_article(fake: LearnFake, slug: str, *, title: str, sort_order: int, featured: bool = False,
                   source: str = "bundle", published_at: Optional[str] = None) -> Dict[str, Any]:
    row = {"id": str(uuid.uuid4()), "slug": slug, "title": title, "category": "blueprints",
           "sort_order": sort_order, "is_featured": featured, "source": source,
           "published_at": published_at,
           "content": {"slug": slug, "title": title, "isFeatured": featured, "category": "blueprints",
                       "sections": []}}
    fake.tables[ARTICLES].rows.append(row)
    return row


def run(coro):
    return asyncio.run(coro)


AFTER = PUBLISH + timedelta(minutes=2)


# ── publish: the happy path ───────────────────────────────────────────────────────────────────


def test_publish_writes_our_row_records_published_and_invalidates(fake, tg, configured, no_narration_module):
    p = make_piece(fake, c.APPROVED)
    gen = MoneyMovesContentService._generation
    out = run(publisher.publish_due(AFTER))
    assert out.get("published") == 1, out
    assert len(fake.articles()) == 1
    row = fake.articles()[0]
    assert row["id"] == publisher.article_id(SLUG) == str(uuid.uuid5(publisher.NS_WEEKLY, SLUG))
    assert row["slug"] == SLUG and row["source"] == "weekly" and row["title"] == TITLE
    assert row["is_featured"] is True and row["content"]["isFeatured"] is True       # equal to the column
    assert row["sort_order"] == -202643 == row["content"]["sortOrder"]
    assert row["audio_url"] == ("https://x.supabase.co/storage/v1/object/public/money-moves-media/"
                                f"audio/weekly/{SLUG}-abcdef012345.m4a")
    assert row["content"]["audioUrl"] == row["audio_url"] and row["content"]["hasAudioVersion"] is True
    assert row["audio_duration_seconds"] == 692 == row["content"]["audioDurationSeconds"]
    assert row["has_audio_version"] is True and row["category"] == "blueprints"
    assert ledger._parse_ts(row["published_at"]) == AFTER
    piece = piece_row(fake, p["id"])
    assert piece["status"] == c.PUBLISHED and ledger._parse_ts(piece["published_at"]) == AFTER
    assert piece["publish_lease_until"] is None
    assert MoneyMovesContentService._generation == gen + 1          # this instance invalidated
    # The silent "Published" reply, threaded under the decision message, with 🗑 Unpublish.
    reply = tg.of("sendMessage")[-1]
    assert reply["disable_notification"] is True and "Published" in reply["text"]
    assert reply["reply_parameters"]["message_id"] == DECISION_ID
    assert reply["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"y:u:{p['id']}"
    assert len(piece["review_message_ids"]) == 2                     # the reply's id joined the fence


def test_the_public_audio_url_delegates_to_narration_when_it_exists(fake, monkeypatch):
    class Narration:
        @staticmethod
        def audio_public_url(path):
            return f"https://narration.example/{path}"

    monkeypatch.setattr(publisher, "_narration_module", lambda: Narration)
    assert publisher.audio_public_url("audio/weekly/a-1.m4a") == "https://narration.example/audio/weekly/a-1.m4a"


def test_not_yet_due_and_after_the_hold_write_nothing(fake, tg, configured, no_narration_module):
    p = make_piece(fake, c.APPROVED)
    assert run(publisher.publish_due(PUBLISH - timedelta(seconds=1))).get("waiting") == 1
    assert run(publisher.publish_due(HOLD)).get("expiring") == 1
    assert fake.articles() == [] and piece_row(fake, p["id"])["status"] == c.APPROVED


def test_a_replayed_publish_writes_nothing_new(fake, tg, configured, no_narration_module):
    """The insert ignores duplicates: a row already there (our own earlier attempt) is adopted."""
    p = make_piece(fake, c.APPROVED)
    row, problem = publisher.build_article_row(piece_row(fake, p["id"]),
                                               fake.row(ledger.JOBS, p["narration_job_id"]), now=AFTER)
    assert problem is None
    fake.tables[ARTICLES].rows.append(copy.deepcopy(row))
    out = run(publisher.publish_due(AFTER))
    assert out.get("published") == 1 and len(fake.articles()) == 1
    assert piece_row(fake, p["id"])["status"] == c.PUBLISHED


# ── dry run ───────────────────────────────────────────────────────────────────────────────────


def test_a_dry_run_piece_is_rehearsed_and_never_written(fake, tg, configured, no_narration_module):
    p = make_piece(fake, c.APPROVED, dry_run=True)
    out = run(publisher.publish_due(AFTER))
    assert out.get("rehearsed") == 1
    assert fake.articles() == []
    assert ("money_move_articles", "upsert") not in fake.calls
    assert piece_row(fake, p["id"])["status"] == c.REHEARSED
    note = tg.of("sendMessage")[-1]
    assert "DRY RUN" in note["text"] and note["disable_notification"] is True


def test_an_unreadable_dry_run_flag_never_publishes(fake, tg, configured, no_narration_module, caplog):
    p = make_piece(fake, c.APPROVED)
    piece_row(fake, p["id"])["dry_run"] = None                  # a corrupt row (NOT NULL in the schema)
    out = run(publisher.publish_due(AFTER))
    assert out.get("failed") == 1 and fake.articles() == []
    assert piece_row(fake, p["id"])["status"] == c.APPROVED
    assert "never published on an unreadable flag" in caplog.text


# ── conflicts and missing inputs ──────────────────────────────────────────────────────────────


def test_a_title_another_article_holds_expires_the_piece_loudly(fake, tg, configured, no_narration_module):
    bundle_article(fake, "older-article", title=TITLE.upper(), sort_order=4)      # case-folded collision
    p = make_piece(fake, c.APPROVED)
    out = run(publisher.publish_due(AFTER))
    assert out.get("expired") == 1, out
    assert [a["slug"] for a in fake.articles()] == ["older-article"]
    piece = piece_row(fake, p["id"])
    assert piece["status"] == c.EXPIRED and piece["last_error"] == "title_or_slug_conflict"
    alert = tg.of("sendMessage")[-1]
    assert "expired" in alert["text"] and "title_or_slug_conflict" in alert["text"]
    assert "disable_notification" not in alert                          # loud


def test_a_slug_held_by_a_bundle_article_is_a_conflict_and_the_bundle_row_is_untouched(
        fake, tg, configured, no_narration_module):
    bundle = bundle_article(fake, SLUG, title="A Different Bundle Title", sort_order=2)
    p = make_piece(fake, c.APPROVED)
    out = run(publisher.publish_due(AFTER))
    assert out.get("expired") == 1
    assert fake.articles() == [bundle] and bundle["source"] == "bundle"
    assert piece_row(fake, p["id"])["last_error"] == "slug_conflict"


@pytest.mark.parametrize("mutate,problem", [
    (lambda p, j: p.update(draft=None), "draft_missing"),
    (lambda p, j: p.update(draft={"title": "x", "sections": []}), "draft_missing"),
    (lambda p, j: p.update(slug=None), "slug_missing"),
    (lambda p, j: p.update(category=None, draft={**draft(), "category": "crypto"}), "category_missing"),
    (lambda p, j: j.update(status="failed"), "narration_missing"),
    (lambda p, j: j.update(audio_path="audio/how-amazon-built-its-moat.m4a"), "narration_path_invalid"),
    (lambda p, j: j.update(audio_path="audio/weekly/../secret.m4a"), "narration_path_invalid"),
    (lambda p, j: j.update(duration_seconds=float("nan")), "narration_duration_invalid"),
    (lambda p, j: j.update(duration_seconds=True), "narration_duration_invalid"),
    (lambda p, j: j.update(duration_seconds=0), "narration_duration_invalid"),
    (lambda p, j: p.update(draft={**draft(), "statistics": [{"value": float("inf")}]}), "draft_not_json"),
])
def test_an_approved_piece_that_can_never_publish_fails_loudly_before_publishing(
        fake, tg, configured, no_narration_module, mutate, problem):
    p = make_piece(fake, c.APPROVED)
    mutate(piece_row(fake, p["id"]), fake.row(ledger.JOBS, p["narration_job_id"]))
    out = run(publisher.publish_due(AFTER))
    assert out.get("failed") == 1, out
    piece = piece_row(fake, p["id"])
    assert piece["status"] == c.FAILED and piece["last_error"] == problem
    assert fake.articles() == []
    assert problem in tg.of("sendMessage")[-1]["text"]


def test_a_pointer_to_a_non_ready_job_falls_back_to_the_newest_ready_round(fake, tg, configured,
                                                                            no_narration_module):
    p = make_piece(fake, c.APPROVED)
    j2 = ledger.enqueue_job_sync(p["id"], round=2, script=[{"i": 0}], script_sha256=SHA2, engine="kokoro",
                                 voice="am_michael", pipeline_version="p1", sb=fake)
    piece_row(fake, p["id"])["narration_job_id"] = j2["id"]          # queued, not ready
    assert run(publisher.publish_due(AFTER)).get("published") == 1
    assert fake.articles()[0]["audio_url"].endswith(f"{SLUG}-abcdef012345.m4a")   # round 1's audio


def test_a_failed_listing_never_raises_and_never_stops_the_other_listing(fake, tg, configured,
                                                                         no_narration_module):
    """The recovery listing (`publishing`) fails; the approved listing still runs in the same tick."""
    p = make_piece(fake, c.APPROVED)
    fake.fail_next(ledger.PIECES, "select", RuntimeError("connection reset"))
    out = run(publisher.publish_due(AFTER))
    assert out == {"failed": 1, "published": 1}, out
    assert piece_row(fake, p["id"])["status"] == c.PUBLISHED


def test_an_insert_failure_leaves_publishing_for_recovery(fake, tg, configured, no_narration_module):
    p = make_piece(fake, c.APPROVED)
    fake.fail_next(ARTICLES, "upsert", RuntimeError("PostgREST 503"))
    out = run(publisher.publish_due(AFTER))
    assert out.get("failed") == 1
    piece = piece_row(fake, p["id"])
    assert piece["status"] == c.PUBLISHING and fake.articles() == []
    # The lease holds a re-run off until it runs out; then recovery redoes the insert.
    assert run(publisher.publish_due(AFTER + timedelta(seconds=60))).get("in_progress") == 1
    later = AFTER + timedelta(seconds=publisher.PUBLISH_LEASE_SECONDS + 1)
    assert run(publisher.publish_due(later)).get("recovered") == 1
    assert piece_row(fake, p["id"])["status"] == c.PUBLISHED and len(fake.articles()) == 1


# ── publishing recovery ───────────────────────────────────────────────────────────────────────


def _stuck_publishing(fake, *, lease: Optional[datetime]) -> Dict[str, Any]:
    p = make_piece(fake, c.PUBLISHING, publish_lease_until=None if lease is None else ledger._iso(lease))
    return p


def test_recovery_records_a_live_row_as_published_even_after_the_hold(fake, tg, configured, no_narration_module):
    p = _stuck_publishing(fake, lease=AFTER)
    row, _ = publisher.build_article_row(piece_row(fake, p["id"]), fake.row(ledger.JOBS, p["narration_job_id"]),
                                         now=AFTER)
    fake.tables[ARTICLES].rows.append(row)
    gen = MoneyMovesContentService._generation
    out = run(publisher.publish_due(HOLD + timedelta(hours=5)))       # hold is ignored for a live row
    assert out.get("recovered") == 1, out
    assert piece_row(fake, p["id"])["status"] == c.PUBLISHED
    assert MoneyMovesContentService._generation == gen + 1


def test_recovery_redoes_a_publish_with_no_row_inside_the_hold(fake, tg, configured, no_narration_module):
    p = _stuck_publishing(fake, lease=None)
    out = run(publisher.publish_due(AFTER))
    assert out.get("recovered") == 1, out
    assert piece_row(fake, p["id"])["status"] == c.PUBLISHED and len(fake.articles()) == 1


def test_recovery_expires_a_publish_with_no_row_after_the_hold(fake, tg, configured, no_narration_module):
    p = _stuck_publishing(fake, lease=AFTER)
    out = run(publisher.publish_due(HOLD + timedelta(minutes=1)))
    assert out.get("expired") == 1
    piece = piece_row(fake, p["id"])
    assert piece["status"] == c.EXPIRED and piece["last_error"] == "publish_not_completed_before_hold_end"
    assert fake.articles() == []
    assert "publish_not_completed_before_hold_end" in tg.of("sendMessage")[-1]["text"]


def test_recovery_leaves_a_running_lease_alone(fake, tg, configured, no_narration_module):
    p = _stuck_publishing(fake, lease=AFTER + timedelta(minutes=3))
    assert run(publisher.publish_due(AFTER)).get("in_progress") == 1
    assert piece_row(fake, p["id"])["status"] == c.PUBLISHING and fake.articles() == []


def test_recovery_with_a_foreign_row_on_the_slug_expires(fake, tg, configured, no_narration_module):
    bundle_article(fake, SLUG, title="Someone Else", sort_order=1)
    p = _stuck_publishing(fake, lease=None)
    assert run(publisher.publish_due(AFTER)).get("expired") == 1
    assert piece_row(fake, p["id"])["last_error"] == "slug_conflict"


# ── expiry ────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", sorted(c.EXPIRABLE_STATUSES))
def test_every_expirable_status_expires_at_the_hold_with_jobs_cancelled_and_an_alert(fake, tg, configured, status):
    p = make_piece(fake, status, job="queued")
    assert run(publisher.expire_due(HOLD - timedelta(seconds=1)))["expired"] == 0
    out = run(publisher.expire_due(HOLD))
    assert out["expired"] == 1
    piece = piece_row(fake, p["id"])
    assert piece["status"] == c.EXPIRED and piece["last_error"] == f"hold_ended_in_{status}"
    assert fake.row(ledger.JOBS, p["narration_job_id"])["status"] == c.JOB_CANCELLED
    line = tg.of("sendMessage")[-1]["text"]
    assert "expired" in line and KEY in line and f"hold_ended_in_{status}" in line


def test_expiry_never_touches_publishing_or_live_pieces(fake, tg, configured):
    live = [make_piece(fake, s, key=f"2026-W{40 + i}:deep_dive") for i, s in
            enumerate((c.PUBLISHING, c.PUBLISHED, c.REHEARSED, c.UNPUBLISHED))]
    out = run(publisher.expire_due(HOLD + timedelta(days=30)))
    assert out == {"expired": 0, "lost": 0, "failed": 0}
    assert [piece_row(fake, p["id"])["status"] for p in live] == [c.PUBLISHING, c.PUBLISHED, c.REHEARSED,
                                                                   c.UNPUBLISHED]


def test_an_expiry_that_loses_the_race_to_an_approve_writes_nothing(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW)

    def approve_first():
        piece_row(fake, p["id"])["status"] = c.APPROVED
    fake.before_update[ledger.PIECES] = [approve_first]
    out = run(publisher.expire_due(HOLD))
    assert out["lost"] == 1 and piece_row(fake, p["id"])["status"] == c.APPROVED


def test_expire_due_survives_an_unreadable_hold(fake, tg, configured, caplog):
    p = make_piece(fake, c.IN_REVIEW)
    piece_row(fake, p["id"])["hold_until"] = "not a time"
    out = run(publisher.expire_due(HOLD))
    assert out["failed"] == 1 and "unreadable hold_until" in caplog.text


# ── unpublish and the featured rule ───────────────────────────────────────────────────────────


def _published(fake, key: str = KEY, *, slug: str = SLUG, title: str = TITLE) -> Dict[str, Any]:
    p = make_piece(fake, c.PUBLISHED, key=key, slug=slug, title=title,
                   the_draft={**draft(title=title), "slug": slug})
    row, problem = publisher.build_article_row(piece_row(fake, p["id"]), fake.row(ledger.JOBS, p["narration_job_id"]),
                                               now=AFTER)
    assert problem is None, problem
    fake.tables[ARTICLES].rows.append(row)
    return p


def test_unpublish_deletes_the_row_and_the_weekly_audio_then_records_it(fake, tg, configured, no_narration_module):
    p = _published(fake)
    bundle_article(fake, "how-amazon-built-its-moat", title="How Amazon Built Its Moat", sort_order=3,
                   featured=True)
    gen = MoneyMovesContentService._generation
    assert run(publisher.unpublish(p["id"], now=AFTER + timedelta(days=1))) is True
    assert [a["slug"] for a in fake.articles()] == ["how-amazon-built-its-moat"]      # bundle row untouched
    piece = piece_row(fake, p["id"])
    assert piece["status"] == c.UNPUBLISHED and piece["unpublished_at"]
    assert fake.storage.removed == [("money-moves-media", [f"audio/weekly/{SLUG}-abcdef012345.m4a"])]
    assert MoneyMovesContentService._generation > gen


def test_unpublish_never_deletes_a_bundle_audio_object(fake, tg, configured, no_narration_module, caplog):
    """The fallback path (no usable audio_url on the live row → the job's path) is fenced too."""
    p = _published(fake)
    fake.articles()[0]["audio_url"] = None
    fake.row(ledger.JOBS, p["narration_job_id"])["audio_path"] = "audio/how-amazon-built-its-moat.m4a"
    assert run(publisher.unpublish(p["id"], now=AFTER)) is True
    assert fake.storage.removed == []
    assert "NOT deleted" in caplog.text
    with pytest.raises(ValueError):
        publisher._remove_audio_sync("audio/how-amazon-built-its-moat.m4a")


def test_unpublish_never_deletes_a_bundle_row_even_when_it_holds_the_slug(fake, tg, configured,
                                                                          no_narration_module):
    """A Studio edit (or a seeder bug) put a BUNDLE row on the weekly slug: the DELETE is fenced on
    source = 'weekly', so the owner's 🗑 can only ever remove what the pipeline published."""
    p = make_piece(fake, c.PUBLISHED)
    bundle = bundle_article(fake, SLUG, title="A Bundle Article", sort_order=2)
    assert run(publisher.unpublish(p["id"], now=AFTER)) is True
    assert fake.articles() == [bundle]


def test_unpublish_of_a_piece_that_is_not_published_does_nothing(fake, tg, configured):
    p = make_piece(fake, c.APPROVED)
    assert run(publisher.unpublish(p["id"], now=AFTER)) is False
    assert piece_row(fake, p["id"])["status"] == c.APPROVED
    assert run(publisher.unpublish("not-a-uuid", now=AFTER)) is False


def test_an_unpublish_whose_ledger_write_fails_is_finished_by_the_next_tap(fake, tg, configured, no_narration_module):
    p = _published(fake)
    fake.fail_next(ledger.PIECES, "update", RuntimeError("PostgREST 503"))
    with pytest.raises(Exception):
        run(publisher.unpublish(p["id"], now=AFTER))
    assert fake.articles() == [] and piece_row(fake, p["id"])["status"] == c.PUBLISHED   # row gone first
    assert run(publisher.unpublish(p["id"], now=AFTER)) is True
    assert piece_row(fake, p["id"])["status"] == c.UNPUBLISHED


def test_the_audio_delete_failing_never_undoes_an_unpublish(fake, tg, configured, no_narration_module):
    p = _published(fake)
    fake.storage.fail_remove = RuntimeError("storage down")
    assert run(publisher.unpublish(p["id"], now=AFTER)) is True
    assert piece_row(fake, p["id"])["status"] == c.UNPUBLISHED


def _served_featured(fake) -> Optional[str]:
    response = run(MoneyMovesContentService()._load())
    featured = [a for a in response.articles if a.get("isFeatured") is True]
    assert len(featured) <= 1
    return featured[0]["slug"] if featured else None


def test_the_newest_deep_dive_wins_and_unpublish_falls_back_never_to_never_feature(fake, tg, configured,
                                                                                    no_narration_module):
    """The featured rule (content service) needs no swap writes: every published Deep Dive keeps its flag,
    the newest (most negative sort_order) wins, an unpublish falls back to the previous one, then to the
    bundle hero — and a NEVER_FEATURE slug never comes back, flagged or not."""
    bundle_article(fake, "the-future-of-digital-finance", title="The Future of Digital Finance", sort_order=0,
                   featured=True)                                   # still flagged: on NEVER_FEATURE
    bundle_article(fake, "how-amazon-built-its-moat", title="How Amazon Built Its Moat", sort_order=3,
                   featured=True)
    w42 = _published(fake, "2026-W42:deep_dive", slug="power-for-ai-2026-10-16", title="Power for AI")
    w43 = _published(fake, KEY)
    assert _served_featured(fake) == SLUG
    assert run(publisher.unpublish(w43["id"], now=AFTER)) is True
    assert _served_featured(fake) == "power-for-ai-2026-10-16"
    assert run(publisher.unpublish(w42["id"], now=AFTER)) is True
    assert _served_featured(fake) == "how-amazon-built-its-moat"
    assert "the-future-of-digital-finance" in mmcs.NEVER_FEATURE


def test_a_slot_piece_is_published_unfeatured_with_a_slot_sort_order(fake, tg, configured, no_narration_module):
    when = schedule.slot_times_for(date(2026, 10, 26))
    p = make_piece(fake, c.APPROVED, key=when.period_key, sched=when.publish_at, hold=when.hold_until)
    assert run(publisher.publish_due(when.publish_at)).get("published") == 1
    row = fake.articles()[0]
    assert row["is_featured"] is False and row["content"]["isFeatured"] is False
    assert row["sort_order"] == 100000 + (date(2026, 10, 26) - date(2026, 1, 1)).days >= 100000
    assert piece_row(fake, p["id"])["status"] == c.PUBLISHED


def test_sort_orders_stay_monotonic_across_week_53():
    assert publisher.sort_order_for({"period_key": "2026-W53:deep_dive", "kind": "deep_dive"}) == -202653
    assert publisher.sort_order_for({"period_key": "2027-W01:deep_dive", "kind": "deep_dive"}) == -202701
    with pytest.raises(ValueError):
        publisher.sort_order_for({"period_key": "2027-W53:deep_dive", "kind": "deep_dive"})
    with pytest.raises(ValueError):
        publisher.sort_order_for({"period_key": KEY, "kind": "slot"})


def test_the_weekly_namespace_never_collides_with_the_seeders():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "scripts" / "seed_money_moves.py").read_text()
    assert 'NS = uuid.UUID("b2c3d4e5-0000-4000-8000-000000000000")' in src
    assert publisher.NS_WEEKLY != uuid.UUID("b2c3d4e5-0000-4000-8000-000000000000")
    assert publisher.NS_WEEKLY == uuid.UUID("317adb60-0daf-5e34-b1ec-dcb9bb52e39a")      # pinned forever


def test_is_weekly_audio_path():
    assert publisher.is_weekly_audio_path("audio/weekly/slug-2026-10-23-abcdef012345.m4a")
    for bad in ("audio/x.m4a", "/audio/weekly/x.m4a", "audio/weekly/../x.m4a", "audio/weekly/", None, 7,
                "audio/weekly/a/b.m4a", "audio/weekly/x.m4a\n"):
        assert not publisher.is_weekly_audio_path(bad), bad


# ── cache sync ────────────────────────────────────────────────────────────────────────────────


def test_cache_sync_follows_the_newest_ledger_stamp(fake, monkeypatch):
    seen = []
    monkeypatch.setattr(MoneyMovesContentService, "sync_with_stamp", classmethod(lambda cls, s: seen.append(s) or True))
    assert run(publisher.cache_sync()) is True and seen == [None]           # nothing ever published
    a = make_piece(fake, c.PUBLISHED, key="2026-W41:deep_dive", slug="a-2026-10-09", title="A")
    b = make_piece(fake, c.UNPUBLISHED, key="2026-W42:deep_dive", slug="b-2026-10-16", title="B")
    piece_row(fake, a["id"])["published_at"] = "2026-10-10T03:00:00.000000Z"
    piece_row(fake, b["id"]).update(published_at="2026-10-17T03:00:00.000000Z",
                                   unpublished_at="2026-10-20T12:00:00.000000Z")
    run(publisher.cache_sync())
    assert seen[-1] == "2026-10-20T12:00:00.000000Z"


def test_cache_sync_invalidates_a_cache_built_before_the_stamp(fake):
    MoneyMovesContentService._cache = (0.0, object())
    MoneyMovesContentService._built_at = datetime(2026, 10, 20, tzinfo=UTC)
    p = make_piece(fake, c.PUBLISHED)
    piece_row(fake, p["id"])["published_at"] = ledger._iso(datetime(2026, 10, 24, 3, tzinfo=UTC))
    gen = MoneyMovesContentService._generation
    assert run(publisher.cache_sync()) is True
    assert MoneyMovesContentService._generation == gen + 1 and MoneyMovesContentService._cache is None


def test_cache_sync_never_raises_on_an_unreadable_ledger(fake, caplog):
    fake.fail_next(ledger.PIECES, "select", RuntimeError("relation does not exist"))
    assert run(publisher.cache_sync()) is None
    assert "ledger stamp unreadable" in caplog.text


def test_publish_due_requires_an_aware_now(fake):
    with pytest.raises(ValueError):
        run(publisher.publish_due(datetime(2026, 10, 24, 3)))


def test_unpublish_deletes_the_audio_the_live_row_points_at(fake, tg, configured, no_narration_module):
    """The live row's audio_url is the truth of what was published: its object is the one removed, even if
    the piece's job pointer moved since."""
    p = _published(fake)
    fake.articles()[0]["audio_url"] = ("https://x.supabase.co/storage/v1/object/public/money-moves-media/"
                                       "audio/weekly/live-copy-0123456789ab.m4a")
    fake.row(ledger.JOBS, p["narration_job_id"])["audio_path"] = "audio/weekly/other-round-ba9876543210.m4a"
    assert run(publisher.unpublish(p["id"], now=AFTER)) is True
    assert fake.storage.removed == [("money-moves-media", ["audio/weekly/live-copy-0123456789ab.m4a"])]


def test_unpublish_never_deletes_a_live_url_outside_the_weekly_folder(fake, tg, configured, no_narration_module):
    p = _published(fake)
    fake.articles()[0]["audio_url"] = ("https://x.supabase.co/storage/v1/object/public/money-moves-media/"
                                       "audio/how-amazon-built-its-moat.m4a")
    fake.row(ledger.JOBS, p["narration_job_id"])["audio_path"] = "audio/how-amazon-built-its-moat.m4a"
    assert run(publisher.unpublish(p["id"], now=AFTER)) is True
    assert fake.storage.removed == []


# ── against the REAL narration module (built beside this one) ─────────────────────────────────


def test_publish_with_the_real_narration_module_stores_the_same_public_url(fake, tg, configured):
    p = make_piece(fake, c.APPROVED)
    assert run(publisher.publish_due(AFTER)).get("published") == 1
    assert fake.articles()[0]["audio_url"] == ("https://x.supabase.co/storage/v1/object/public/money-moves-media/"
                                               f"audio/weekly/{SLUG}-abcdef012345.m4a")
    assert piece_row(fake, p["id"])["status"] == c.PUBLISHED


def test_a_path_the_narration_module_refuses_fails_the_piece_instead_of_retrying_forever(fake, tg, configured):
    p = make_piece(fake, c.APPROVED)
    fake.row(ledger.JOBS, p["narration_job_id"])["audio_path"] = "audio/weekly/Not_Kebab-XYZ.m4a"
    assert run(publisher.publish_due(AFTER)).get("failed") == 1
    assert piece_row(fake, p["id"])["last_error"] == "narration_path_invalid" and fake.articles() == []


# ── cover art (owner 2026-11-01, `cover_art.py`) ──────────────────────────────────────────────

HERO_URL = f"https://x.supabase.co/storage/v1/object/public/money-moves-images/articles/{SLUG}.hero.jpg"
CARD_URL = f"https://x.supabase.co/storage/v1/object/public/money-moves-images/articles/{SLUG}.card.jpg"


def test_cover_art_reaches_the_image_url_column_and_the_served_catalog(fake, tg, configured,
                                                                     no_narration_module):
    d = {**draft(), "imageUrl": HERO_URL, "imageCardUrl": CARD_URL}
    make_piece(fake, c.APPROVED, the_draft=d)
    assert run(publisher.publish_due(AFTER)).get("published") == 1
    row = fake.articles()[0]
    assert row["image_url"] == HERO_URL
    assert row["content"]["imageUrl"] == HERO_URL and row["content"]["imageCardUrl"] == CARD_URL
    served = [a for a in run(MoneyMovesContentService()._load()).articles if a.get("slug") == SLUG]
    assert served and served[0]["imageUrl"] == HERO_URL and served[0]["imageCardUrl"] == CARD_URL


def test_an_artless_draft_publishes_with_a_null_image_url(fake, tg, configured, no_narration_module):
    make_piece(fake, c.APPROVED)
    assert run(publisher.publish_due(AFTER)).get("published") == 1
    row = fake.articles()[0]
    assert row["image_url"] is None and "imageUrl" not in row["content"]


@pytest.mark.parametrize("junk", ["", "javascript:alert(1)", "ftp://x/y.jpg", 7, ["x"], "https://" + "x" * 1001],
                         ids=["empty", "javascript", "ftp", "int", "list", "too-long"])
def test_a_junk_image_url_is_never_published(fake, junk, caplog):
    p = make_piece(fake, c.APPROVED, the_draft={**draft(), "imageUrl": junk, "imageCardUrl": CARD_URL})
    with caplog.at_level(logging.WARNING):
        row, problem = publisher.build_article_row(piece_row(fake, p["id"]),
                                                   fake.row(ledger.JOBS, p["narration_job_id"]), now=AFTER)
    assert problem is None and row["image_url"] is None
    assert "imageUrl" not in row["content"] and "imageCardUrl" not in row["content"]
    assert any("unusable imageUrl" in r.getMessage() for r in caplog.records)
    # pure: the stored draft is untouched
    assert piece_row(fake, p["id"])["draft"]["imageUrl"] == junk
