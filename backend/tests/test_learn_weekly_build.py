"""The BUILD step (`app/services/learn_weekly/build.py`).

Hermetic, and deliberately against the REAL ledger: every write goes through `ledger.py` into the
ledger suite's in-memory PostgREST fake (which enforces migration 204's UNIQUE keys and CHECKs), so
the lease, the generation fence, the counters and the status rules are the production ones. Stubbed:
SEC reads (`sec_facts`), the case library, the served catalog, the writer (except in the one
end-to-end test, which runs the real writer on a fake Gemini client), narration and alerts.

What is pinned, and why:
  * exactly-once is the lease (two concurrent builds → one generation writes);
  * accepted → drafted (draft, its sha, the fact sheet, subjects, category, validation, tokens) →
    narration queued → media_pending, every write fenced on the generation;
  * the two caps are separate: content rejections end `skipped`, writer failures end `failed`, each
    with a loud alert; an unavailable fact sheet or catalog never counts against either;
  * the catch-up cut-off ends a retry loop loudly; a lost lease writes nothing;
  * the known ledger conflict (contract 4 wants `building → planned`, the ledger refuses it) is
    handled visibly: the piece stays `building` with its lease cleared and an ERROR is logged — and the
    moment the ledger allows the move, the build takes it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from test_learn_weekly_ledger import FakeSupabase

from app.config import settings
from app.integrations.sec_edgar import SECConfigException, SECUnavailableException
from app.services.learn_weekly import article_schema as A
from app.services.learn_weekly import build as B
from app.services.learn_weekly import case_library as CL
from app.services.learn_weekly import contracts as C
from app.services.learn_weekly import fact_sheet as fs
from app.services.learn_weekly import ledger, sec_facts
from app.services.learn_weekly import writer as W

UTC = timezone.utc
T0 = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)            # Fri 2026-10-23 23:00 ET (publish)
HOLD = T0 + timedelta(hours=48)
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)          # Fri 08:00 ET (build)
KEY = "2026-W43:deep_dive"
CIK = "0001045810"
ACCN = "0001045810-25-000023"
BUNDLE = Path(__file__).resolve().parents[2] / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"

PEG = C.Fact("P7", "On October 21, 2026, NVIDIA CORP announced a supply agreement for data center "
                   "systems.", C.ORIGIN_SEC_PEG, "https://www.sec.gov/Archives/x",
             "0001045810-26-000101")
# The 8-K the peg spans come from, as SEC's submissions record lists it: it dates the peg facts.
PEG_8K = sec_facts.Filing8K("2026-10-21", PEG.accession, "nvda-8k.htm", ("8.01",))
XBRL = [
    C.Fact("S1", "NVIDIA CORP reported revenue of $130.5 billion for the fiscal year ended January "
                 "26, 2025 (Form 10-K).", C.ORIGIN_SEC_XBRL, "https://www.sec.gov/b", ACCN),
    C.Fact("S2", "NVIDIA CORP reported research and development expense of $12.9 billion for the "
                 "fiscal year ended January 26, 2025 (Form 10-K).", C.ORIGIN_SEC_XBRL,
           "https://www.sec.gov/b", ACCN),
    C.Fact("S3", "NVIDIA CORP reported net income of $72.9 billion for the fiscal year ended January "
                 "26, 2025 (Form 10-K).", C.ORIGIN_SEC_XBRL, "https://www.sec.gov/b", ACCN),
    C.Fact("S4", "NVIDIA CORP reported cash and cash equivalents of $8.6 billion as of January 26, "
                 "2025 (Form 10-K).", C.ORIGIN_SEC_XBRL, "https://www.sec.gov/b", ACCN),
]


def _entry(eid: str, themes, cik: Optional[str], kind: str = "mechanics",
           text: str = "A data center needs land, power and cooling before it can run.") -> CL.LibraryEntry:
    src = CL.LibrarySource(title="A primer", url="https://www.sec.gov/primer")
    return CL.LibraryEntry(
        id=eid, kind=kind, title=eid, themes=tuple(themes), category_fit=("blueprints",),
        companies=(CL.LibraryCompany(name="NVIDIA CORP", cik=cik),) if cik else (
            CL.LibraryCompany(name="Some Builder", cik="0000000001"),),
        sic=None, era=None, status="resolved", llm_eligible=True,
        facts=(CL.LibraryFact(id="f1", text=text, source=src),),
        verified_by="editor", verified_at="2026-10-01")


LIBRARY = CL.CaseLibrary(version=1, entries=(
    _entry("dc-primer", ["ai-chips"], None),
    _entry("nvda-primer", ["other-theme"], CIK,
           text="The company designs chips and buys their manufacturing from partners."),
    _entry("unrelated", ["other-theme"], None, text="An unrelated primer about something else."),
    _entry("a-case", ["ai-chips"], None, kind="case", text="A historical case on the theme."),
))


def candidate(**over: Any) -> Dict[str, Any]:
    base = {
        **C.TopicCandidate(kind="theme", key="ai-chips", title="AI chips", score=3.2,
                           tickers=("NVDA",)).to_json(),
        "label": "AI chip supply",
        "subjects": [{"name": "NVIDIA CORP", "cik": "1045810", "ticker": "nvda"}],
        "peg_facts": [vars(PEG)],
        "category": "blueprints",
    }
    base.update(over)
    return base


def topic(chosen: Optional[int] = 0, cands: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"version": 1, "candidates": cands if cands is not None else [candidate(),
            candidate(key="power", title="Power for AI", label="Power for AI", category="battles")],
            "chosen_index": chosen, "owner_pick": None, "auto_picked": chosen is not None,
            "demand_context": {}, "scored_at": "2026-10-22T22:00:00+00:00"}


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake(monkeypatch):
    sb = FakeSupabase()
    monkeypatch.setattr(ledger, "get_supabase", lambda: sb)
    return sb


class Recorder:
    def __init__(self) -> None:
        self.terminal: List[Dict[str, Any]] = []
        self.notes: List[str] = []
        self.enqueued: List[Dict[str, Any]] = []
        self.enqueue_results: List[Any] = []

    async def notify_terminal(self, piece, *, status, reason, stage=None):
        self.terminal.append({"id": piece.get("id"), "status": status, "reason": reason,
                              "stage": stage, "row_status": piece.get("status")})

    async def notify(self, text, *, loud=True):
        self.notes.append(text)
        return True

    async def enqueue_for_piece(self, piece, content, *, engine, round):
        self.enqueued.append({"piece": piece.get("id"), "engine": engine, "round": round,
                              "content": content})
        if self.enqueue_results:
            r = self.enqueue_results.pop(0)
            if isinstance(r, BaseException):
                raise r
            return r
        return {"id": str(uuid.uuid4()), "round": round}

    def current_engine(self, fallback=None):
        # The REAL fallback rule: the build must ask narration, never read the raw setting.
        from app.services.learn_weekly import narration
        return narration.current_engine(fallback)


class ArticlesFake:
    """`money_move_articles` as PostgREST serves it to the build's title read: `select` → `order`
    → `range` → `execute`, each page clamped to the server's 1,000 rows (the clamp is what makes a
    single unpaged read silently short)."""

    CLAMP = 1000

    def __init__(self, rows=None):
        self.rows: List[Dict[str, Any]] = list(rows or [])
        self.selects: List[str] = []
        self.ranges: List[tuple] = []
        self.fail: Optional[Exception] = None

    def table(self, name):
        assert name == "money_move_articles", name
        return _ArticlesQuery(self)


class _ArticlesQuery:
    def __init__(self, fake: ArticlesFake):
        self.fake, self.cols, self.order_col, self.desc, self.span = fake, None, None, False, None

    def select(self, cols):
        self.cols = cols
        self.fake.selects.append(cols)
        return self

    def order(self, col, *, desc=False, **_):
        self.order_col, self.desc = col, desc
        return self

    def range(self, a, b):
        self.span = (a, b)
        self.fake.ranges.append((a, b))
        return self

    def execute(self):
        if self.fake.fail is not None:
            raise self.fake.fail
        rows = sorted(self.fake.rows, key=lambda r: r[self.order_col], reverse=self.desc)
        a, b = self.span if self.span else (0, len(rows) - 1)
        page = rows[a:b + 1][: ArticlesFake.CLAMP]
        cols = [c.strip() for c in self.cols.split(",")]
        return SimpleNamespace(data=[{c: r.get(c) for c in cols} for r in page])


def article_rows(n: int, *, start: int = 0) -> List[Dict[str, Any]]:
    return [{"id": f"{i:08d}-0000-0000-0000-000000000000", "title": f"Archived Piece {i}",
             "slug": f"archived-piece-{i}-2024-01-01"} for i in range(start, start + n)]


@pytest.fixture
def rec(monkeypatch, fake):
    r = Recorder()
    monkeypatch.setattr(B, "_alerts", lambda: r)
    monkeypatch.setattr(B, "_narration", lambda: r)
    r.articles = ArticlesFake()
    # The build's own binding (a module-level import), never app.database's.
    monkeypatch.setattr(B, "get_supabase", lambda: r.articles)
    B._ENQUEUE_ALERTED.clear()

    async def summary(cik, *, client=None):
        return sec_facts.CompanySummary(name="NVIDIA CORP", cik=CIK, tickers=("NVDA",),
                                        exchanges=("Nasdaq",), sic="3674", sic_description=None,
                                        foreign_filer=False, recent_8k=(PEG_8K,))

    async def annual(cik, name, *, client=None):
        return list(XBRL)

    monkeypatch.setattr(sec_facts, "company_summary", summary)
    monkeypatch.setattr(sec_facts, "annual_facts", annual)
    monkeypatch.setattr(CL, "get_case_library", lambda path=None: LIBRARY)

    async def catalog():
        return json.loads(BUNDLE.read_text(encoding="utf-8"))["articles"]

    monkeypatch.setattr(B, "_served_catalog", catalog)
    # The retry decision reads the wall clock; pin it, or this suite becomes a time bomb once the
    # real date passes the fixtures' cut-off.
    monkeypatch.setattr(B, "_utcnow", lambda: NOW)
    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", "chatterbox")
    monkeypatch.setattr(settings, "LEARN_MAX_CONTENT_REJECTIONS", 2)
    monkeypatch.setattr(settings, "LEARN_MAX_WRITER_FAILURES", 3)
    return r


def make_piece(fake, *, key: str = KEY, tp: Any = "default", status: str = C.PLANNED,
               **fields: Any) -> Dict[str, Any]:
    kind = "deep_dive" if key.endswith("deep_dive") else "slot"
    row = ledger.create_piece_sync(key, kind, scheduled_publish_at=T0, hold_until=HOLD,
                                   dry_run=True, sb=fake)
    patch = dict(fields)
    patch["topic"] = topic() if tp == "default" else tp
    ledger.transition_sync(row["id"], from_statuses=[C.PLANNED], to_status=C.PLANNED, fields=patch,
                           sb=fake)
    if status != C.PLANNED:
        fake.row(ledger.PIECES, row["id"])["status"] = status
    return dict(fake.row(ledger.PIECES, row["id"]))


def stored(fake, pid) -> Dict[str, Any]:
    return dict(fake.row(ledger.PIECES, pid))


def content_for(title: str = "How Chips Became the Bottleneck") -> Dict[str, Any]:
    o = A.normalize_outline(A.parse_outline({
        "title": title, "subtitle": "A subtitle.", "cardSubtitle": "Card.", "tagLabel": "Case Study",
        "keyHighlights": [{"icon": "cpu.fill", "title": f"H{i}", "description": "D."}
                          for i in range(3)],
        "sections": [{"title": f"S{i}", "icon": "cpu.fill", "kind": "history", "fact_ids": [],
                      "target_words": 300} for i in range(5)],
        "statistics": []}), "deep_dive")
    sheet = C.FactSheet("deep_dive", "t", None, (), ())
    return A.append_disclosure(A.render_ios_content(
        o, [[{"type": "paragraph", "text": "Body text."}]] * 5, kind="deep_dive",
        category="blueprints", slug=A.make_slug(title, date(2026, 10, 23)),
        published_date=date(2026, 10, 23), sheet=sheet))


class FakeWriter:
    """Stands in for `writer.write_article`: records its kwargs, optionally exercises
    `before_call` / `on_progress`, and returns the next scripted outcome (or raises it)."""

    def __init__(self, *outcomes: Any, before_calls: int = 1, progress: Optional[Dict] = None,
                 hook=None) -> None:
        self.outcomes = list(outcomes)
        self.before_calls, self.progress, self.hook = before_calls, progress, hook
        self.calls: List[Dict[str, Any]] = []
        self.before_answers: List[bool] = []

    async def __call__(self, **kw: Any) -> W.WriterOutcome:
        self.calls.append(kw)
        if self.hook is not None:
            await self.hook(kw)
        for _ in range(self.before_calls):
            self.before_answers.append(await kw["before_call"]())
        if self.progress is not None:
            await kw["on_progress"](self.progress)
        out = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(out, BaseException):
            raise out
        if self.before_answers and self.before_answers[-1] is False:
            return W.WriterOutcome(status=W.STATUS_LEASE_LOST, error="lease lost before outline",
                                   tokens_used=0, calls=0)
        return out


def accepted(content: Optional[Dict[str, Any]] = None, tokens: int = 1234) -> W.WriterOutcome:
    return W.WriterOutcome(status=W.STATUS_ACCEPTED, content=content or content_for(),
                           section_kinds=["history"] * 5, judge={"mode": "shadow", "verdicts": []},
                           tokens_used=tokens, calls=7, model="gemini-2.5-flash")


def rejected(tokens: int = 300) -> W.WriterOutcome:
    return W.WriterOutcome(status=W.STATUS_CONTENT_REJECTED,
                           issues=[{"field": "title", "code": "title_collision", "detail": "x"}],
                           tokens_used=tokens, calls=3)


def failed(tokens: int = 50) -> W.WriterOutcome:
    return W.WriterOutcome(status=W.STATUS_WRITER_FAILED, error="outline: truncated",
                           tokens_used=tokens, calls=1)


def build(piece: Dict[str, Any], now: datetime = NOW) -> Optional[Dict[str, Any]]:
    return asyncio.run(B.build_piece(piece, now=now))


def allow_building_to_planned(monkeypatch):
    """What the ledger would do once its rule admits `building → planned` (contract 4)."""
    real = ledger.transition_problem

    def patched(froms, to):
        froms = list(froms)
        if to == C.PLANNED and froms and all(f == C.BUILDING for f in froms):
            return None
        return real(froms, to)

    monkeypatch.setattr(ledger, "transition_problem", patched)


# ═════════════════════════════════════════════════════════════════════════════════════════════
# A. Accepted: drafted → narration queued → media_pending
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_an_accepted_draft_is_stored_and_its_narration_queued(fake, rec, monkeypatch):
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    out = build(piece)
    row = stored(fake, piece["id"])
    assert out is not None and row["status"] == C.MEDIA_PENDING
    content = fw.outcomes[0].content
    assert row["draft"] == content and row["draft_sha256"] == A.content_sha256(content)
    assert row["slug"] == content["slug"] and row["title"] == content["title"]
    assert row["category"] == "blueprints"
    sheet = C.FactSheet.from_json(row["fact_sheet"])
    assert row["fact_sheet_sha256"] == B.sheet_sha256(sheet)
    ids = [f.id for f in sheet.facts]
    assert ids[:5] == ["P1", "S1", "S2", "S3", "S4"], "peg first, renumbered; filings next"
    assert "L:dc-primer:f1" in ids and "L:nvda-primer:f1" in ids
    assert "L:unrelated:f1" not in ids and "L:a-case:f1" not in ids, "primers of the theme/subject only"
    assert row["subjects"] == [{"name": "NVIDIA CORP", "cik": CIK, "ticker": "NVDA"}]
    assert row["validation"]["outcome"] == "accepted" and row["tokens_used"] == 1234
    assert row["lease_until"] is None and row["generations"] == 1
    assert row["narration_job_id"] and row["last_stage"] == "narration_queued"
    assert row["progress"] == {} and row["last_error"] is None
    (q,) = rec.enqueued
    assert q["engine"] == "chatterbox" and q["round"] == 1 and q["content"] == content
    kw = fw.calls[0]
    assert kw["kind"] == "deep_dive" and kw["category"] == "blueprints"
    assert kw["topic_label"] == "AI chip supply" and kw["published_date"] == date(2026, 10, 23)
    assert kw["generation_id"] == row["generation_id"]
    assert "The Fall of Sears" in kw["served_titles"] and kw["catalog"]
    assert rec.terminal == []


def test_before_call_refreshes_the_held_lease(fake, rec, monkeypatch):
    fw = FakeWriter(accepted(), before_calls=3)
    monkeypatch.setattr(W, "write_article", fw)
    build(make_piece(fake))
    assert fw.before_answers == [True, True, True]


def test_the_real_writer_end_to_end_on_a_fake_model(fake, rec, monkeypatch):
    """The real writer, validators and judge (shadow) on a fake Gemini: the build's sheet, catalog
    and in-flight reads line up with what the writer and validators expect."""
    import test_learn_weekly_writer as TW
    from app.integrations import gemini

    peg = C.Fact("P9", TW.PEG_LINE, C.ORIGIN_SEC_PEG, "https://www.sec.gov/Archives/x",
                 "0001045810-26-000101")
    client = TW.FakeClient()
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    monkeypatch.setattr(settings, "LEARN_JUDGE_MODE", "shadow")
    piece = make_piece(fake, tp=topic(cands=[candidate(peg_facts=[vars(peg)])]))
    build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.MEDIA_PENDING, (row["last_error"], row["validation"])
    assert client.stages_before_support == ["outline"] + [f"section {i}" for i in range(1, 6)] + ["judge"]
    assert client.support_calls >= 1 and row["validation"]["judge"]["support"]["flagged"] == 0
    assert row["draft"]["sections"][-1]["content"][-1] == A.disclosure_block()
    assert row["validation"]["section_kinds"][0] == "peg"
    assert row["tokens_used"] == row["validation"]["tokens_used"] > 0
    assert row["progress"] == {}, "progress is cleared once the draft is written"


# ═════════════════════════════════════════════════════════════════════════════════════════════
# B. Exactly-once and the lease
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_two_concurrent_builds_make_one_generation(fake, rec, monkeypatch):
    events: Dict[str, asyncio.Event] = {}

    async def hold(_kw):
        events["entered"].set()          # the first build holds the lease and is writing
        await events["gate"].wait()

    fw = FakeWriter(accepted(), hook=hold)
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)

    async def both():
        events["entered"], events["gate"] = asyncio.Event(), asyncio.Event()
        first = asyncio.create_task(B.build_piece(piece, now=NOW))
        await asyncio.wait_for(events["entered"].wait(), timeout=10)
        second = await B.build_piece(piece, now=NOW)
        events["gate"].set()
        return await first, second

    first, second = asyncio.run(both())
    assert second is None and first is not None
    assert len(fw.calls) == 1 and len(rec.enqueued) == 1
    assert stored(fake, piece["id"])["generations"] == 1


def test_a_lost_lease_writes_nothing(fake, rec, monkeypatch):
    other = str(uuid.uuid4())

    async def takeover(kw):
        # Another generation took the piece over (its lease expired under us).
        fake.row(ledger.PIECES, piece["id"])["generation_id"] = other

    fw = FakeWriter(accepted(), hook=takeover)
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    assert build(piece) is None
    row = stored(fake, piece["id"])
    assert fw.before_answers == [False]
    assert row["generation_id"] == other and row["status"] == C.BUILDING
    assert row["draft"] is None and rec.enqueued == [] and rec.terminal == []


def test_a_refresh_that_raises_stops_the_generation(fake, rec, monkeypatch):
    async def boom(*a, **k):
        raise ledger.LearnLedgerError("supabase down")

    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    monkeypatch.setattr(ledger, "refresh_lease", boom)
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert fw.before_answers == [False]
    assert row["draft"] is None and row["lease_until"] is None
    assert row["last_error"].startswith("lease_lost")
    assert row["content_rejections"] == row["writer_failures"] == 0


def test_a_building_piece_with_a_live_lease_is_left_alone(fake, rec, monkeypatch):
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    ledger.acquire_lease_sync(piece["id"], generation_id=str(uuid.uuid4()), lease_seconds=900,
                              from_statuses=[C.PLANNED], to_status=C.BUILDING, now=NOW, sb=fake)
    assert build(stored(fake, piece["id"])) is None
    assert fw.calls == []


# ═════════════════════════════════════════════════════════════════════════════════════════════
# C. Retries and the two caps
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_content_rejections_retry_then_skip_with_an_alert(fake, rec, monkeypatch, caplog):
    fw = FakeWriter(rejected(300), rejected(200), progress={"outline": {"title": "x"}, "sections": []})
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    with caplog.at_level(logging.ERROR):
        build(piece)
    row = stored(fake, piece["id"])
    # Contract 4: a retry hands the piece back to the build queue with its lease cleared.
    assert row["status"] == C.PLANNED and row["lease_until"] is None
    assert "refuses building → planned" not in caplog.text
    assert row["content_rejections"] == 1 and row["writer_failures"] == 0
    assert row["last_error"].startswith("content_rejections 1/2: content_rejected")
    assert row["progress"] == {}, "a rejected draft is never resumed"
    assert row["validation"]["outcome"] == "content_rejected"
    assert row["tokens_used"] == 300
    build(row)                                          # the next tick re-acquires and rejects again
    row = stored(fake, piece["id"])
    assert row["status"] == C.SKIPPED and row["content_rejections"] == 2
    assert row["generations"] == 2 and row["tokens_used"] == 500
    assert rec.terminal == [{"id": piece["id"], "status": C.SKIPPED,
                             "reason": "content_rejections_exhausted", "stage": "build",
                             "row_status": C.SKIPPED}]


def test_once_the_ledger_allows_it_a_retry_goes_back_to_planned(fake, rec, monkeypatch, caplog):
    allow_building_to_planned(monkeypatch)
    monkeypatch.setattr(W, "write_article", FakeWriter(rejected()))
    piece = make_piece(fake)
    with caplog.at_level(logging.ERROR):
        build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.PLANNED and row["lease_until"] is None
    assert "refuses building" not in caplog.text


def test_writer_failures_have_their_own_cap_and_end_failed(fake, rec, monkeypatch):
    progress = {"outline": {"title": "kept"}, "sections": []}
    fw = FakeWriter(failed(10), failed(20), failed(30), progress=progress)
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    for _ in range(2):
        build(stored(fake, piece["id"]))
        row = stored(fake, piece["id"])
        assert row["status"] == C.PLANNED and row["progress"]["outline"] == {"title": "kept"}
        assert row["content_rejections"] == 0
    build(stored(fake, piece["id"]))
    row = stored(fake, piece["id"])
    assert row["status"] == C.FAILED and row["writer_failures"] == 3 and row["tokens_used"] == 60
    assert row["last_error"].startswith("writer_failures_exhausted: writer_failed")
    assert [t["reason"] for t in rec.terminal] == ["writer_failures_exhausted"]


def test_a_crash_in_the_writer_counts_as_a_writer_failure(fake, rec, monkeypatch):
    monkeypatch.setattr(W, "write_article", FakeWriter(KeyError("bug")))
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert row["writer_failures"] == 1 and "crash: KeyError" in row["last_error"]
    assert row["lease_until"] is None


def test_an_unavailable_fact_sheet_retries_without_counting(fake, rec, monkeypatch):
    async def down(cik, name, *, client=None):
        raise SECUnavailableException("503 from SEC")

    monkeypatch.setattr(sec_facts, "annual_facts", down)
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert fw.calls == [] and row["content_rejections"] == row["writer_failures"] == 0
    assert row["last_error"].startswith("fact_sheet_unavailable") and "503" in row["last_error"]
    assert row["status"] == C.PLANNED and row["lease_until"] is None and rec.terminal == []


def test_past_the_cutoff_a_retry_is_skipped_loudly(fake, rec, monkeypatch):
    async def down(cik, name, *, client=None):
        raise SECUnavailableException("503 from SEC")

    monkeypatch.setattr(sec_facts, "annual_facts", down)
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))
    piece = make_piece(fake)
    cutoff = B.catch_up_cutoff_for(piece)
    # A tick just before the cut-off starts the build; the retry decision is taken at its `now`.
    build(piece, now=cutoff - timedelta(seconds=1))
    assert stored(fake, piece["id"])["status"] == C.PLANNED
    build(stored(fake, piece["id"]), now=cutoff)
    row = stored(fake, piece["id"])
    assert row["status"] == C.SKIPPED and row["last_error"] == "catch_up_cutoff_passed"
    assert [t["reason"] for t in rec.terminal] == ["catch_up_cutoff_passed"]


def test_a_build_that_runs_past_the_cutoff_skips_instead_of_retrying(fake, rec, monkeypatch):
    """The tick started before the cut-off; by the time the generation was rejected the wall clock
    had passed it: no retry could leave the owner a review window, so it ends loudly now."""
    monkeypatch.setattr(W, "write_article", FakeWriter(rejected(70)))
    piece = make_piece(fake)
    cutoff = B.catch_up_cutoff_for(piece)
    monkeypatch.setattr(B, "_utcnow", lambda: cutoff + timedelta(minutes=8))
    build(piece, now=cutoff - timedelta(minutes=2))
    row = stored(fake, piece["id"])
    assert row["status"] == C.SKIPPED and row["content_rejections"] == 1
    assert row["last_error"].startswith("catch_up_cutoff_passed after content_rejections 1/2")
    assert row["tokens_used"] == 70 and row["lease_until"] is None
    assert [t["reason"] for t in rec.terminal] == ["catch_up_cutoff_passed"]


def schedule_cutoff(piece):
    from app.services.learn_weekly import schedule
    return schedule.catch_up_cutoff(HOLD, "chatterbox", C.DEEP_DIVE_WORDS[1])


def test_an_empty_catalog_waits_rather_than_failing_open(fake, rec, monkeypatch):
    async def empty():
        return []

    monkeypatch.setattr(B, "_served_catalog", empty)
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert fw.calls == [] and row["last_error"].startswith("catalog_unavailable")


# ── A lone company needs its verified company primer (owner decision 2026-10-24) ────────────

NVDA_SUBJECTS = [C.Subject("NVIDIA CORP", CIK, "NVDA")]
NVDA_COMPANY_LIB = CL.CaseLibrary(version=1, entries=(_entry("co-nvidia", ["other-theme"], CIK, "company"),))


def lone(**over: Any) -> Dict[str, Any]:
    """A single-company candidate (kind ticker) about NVIDIA."""
    return candidate(**{**C.TopicCandidate(kind="ticker", key="NVDA", title="NVDA", score=4.0,
                                           tickers=("NVDA",)).to_json(), "label": "NVIDIA", **over})


@pytest.mark.parametrize("cand,lib,refused", [
    (lone(), NVDA_COMPANY_LIB, False),
    (lone(), CL.CaseLibrary(1, ()), True),
    (lone(), CL.CaseLibrary(1, (_entry("mech", ["ai-chips"], CIK),)), True),       # mechanics only
    (lone(), CL.CaseLibrary(1, (CL.LibraryEntry(**{**vars(_entry("co-nvidia", ["x"], CIK, "company")),
                                                   "verified_by": None}),)), True),   # unverified
    (candidate(), CL.CaseLibrary(1, ()), False),                                    # a theme: never refused here
    ({**lone(), "kind": None}, CL.CaseLibrary(1, ()), True),                        # unknown kind: fail closed
])
def test_a_lone_company_without_its_verified_primer_is_refused(cand, lib, refused):
    problem = B.single_company_problem(cand, NVDA_SUBJECTS, lib)
    assert (problem is not None) is refused
    if refused:
        assert "NVIDIA CORP has no verified company entry" in problem


MMM_SUBJECT = C.Subject("3M CO", "0000066740", "MMM")


@pytest.mark.parametrize("subjects,refused", [
    ([MMM_SUBJECT, NVDA_SUBJECTS[0]], True),     # an unprimed first subject
    ([NVDA_SUBJECTS[0], MMM_SUBJECT], True),     # an unprimed one behind a primed one (review 2026-10-24)
    ([NVDA_SUBJECTS[0]], False),
    ([], False),                                  # no subject: the ordinary shortfall check says so
])
def test_every_main_subject_of_a_lone_company_topic_needs_its_primer(subjects, refused):
    problem = B.single_company_problem(lone(), subjects, NVDA_COMPANY_LIB)
    assert (problem is not None) is refused
    if refused:
        assert problem.count("3M CO") == 1 and "NVIDIA CORP" not in problem


def test_the_guard_runs_before_any_sec_read(rec, monkeypatch):
    calls: List[str] = []

    async def summary(cik, *, client=None):
        calls.append(cik)
        return None

    monkeypatch.setattr(sec_facts, "company_summary", summary)
    with pytest.raises(B.FactSheetShortfall, match="no verified company entry"):
        asyncio.run(B.assemble_fact_sheet("deep_dive", lone(), library=CL.CaseLibrary(1, ())))
    assert calls == [], "no SEC read is spent on a refused candidate"


def test_a_stored_lone_company_without_a_primer_moves_to_the_next_candidate(fake, rec, monkeypatch):
    """A topic stored before the rule (or a primer unverified since): the build passes the lone
    company over exactly like a thin sheet — the topic moves to #2 with one alert."""
    monkeypatch.setattr(CL, "get_case_library", lambda path=None: CL.CaseLibrary(1, ()))
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake, tp=topic(cands=[lone(), candidate(key="power", title="Power for AI",
                                                                label="Power for AI")]))
    build(piece)
    row = stored(fake, piece["id"])
    assert fw.calls == [] and row["draft"] is None and row["status"] == C.PLANNED
    assert row["last_error"].startswith("no_company_primer: #1 'NVIDIA'")
    assert "no verified company entry" in row["last_error"] and "moved to #2 'Power for AI'" in row["last_error"]
    t = row["topic"]
    assert t["chosen_index"] == 1 and [(d["index"], d["key"]) for d in t["build_drops"]] == [(0, "ticker:NVDA")]
    # Named for what it is: no filing was read, so never "only 0 usable facts in its filings".
    (note,) = rec.notes
    assert "has no verified company entry in the case library — switched to #2" in note
    assert "usable facts" not in note and rec.terminal == []


def test_a_lone_company_left_as_the_last_candidate_ends_skipped_as_no_company_primer(fake, rec, monkeypatch):
    monkeypatch.setattr(CL, "get_case_library", lambda path=None: CL.CaseLibrary(1, ()))
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))
    piece = make_piece(fake, tp=topic(cands=[lone()]))
    build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.SKIPPED and row["last_error"].startswith("no_company_primer: #1 'NVIDIA'")
    assert [t["reason"] for t in rec.terminal] == ["no_company_primer"] and rec.notes == []


def test_a_primed_lone_company_passes_the_guard_and_is_written(fake, rec, monkeypatch):
    """The other side of the rule: with its verified company primer a lone company is built — the
    sheet carries the primer's facts and the writer is called, with the owner's pick unchanged."""
    sheet = asyncio.run(B.assemble_fact_sheet("deep_dive", lone(), library=NVDA_COMPANY_LIB))
    assert any(f.id.startswith("L:co-nvidia:") for f in sheet.facts)
    monkeypatch.setattr(CL, "get_case_library", lambda path=None: NVDA_COMPANY_LIB)
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake, tp=topic(cands=[lone(), candidate(key="power", label="Power for AI")]))
    build(piece)
    row = stored(fake, piece["id"])
    assert len(fw.calls) == 1 and row["topic"]["chosen_index"] == 0 and row["status"] == C.MEDIA_PENDING


def test_a_thin_sheet_is_never_written_and_moves_to_the_next_candidate(fake, rec, monkeypatch):
    """Review F3: every SEC read answered and the sheet is still thin — a property of candidate #1,
    not an outage. It must not be retried for two days; the topic moves to #2 with one alert."""
    async def none(cik, name, *, client=None):
        return []

    monkeypatch.setattr(sec_facts, "annual_facts", none)
    monkeypatch.setattr(CL, "get_case_library", lambda path=None: CL.CaseLibrary(1, ()))
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    tp = topic()
    tp["owner_pick"], tp["auto_picked"] = 0, False          # the owner tapped #1
    piece = make_piece(fake, tp=tp)
    build(piece)
    row = stored(fake, piece["id"])
    assert fw.calls == [] and row["draft"] is None
    assert row["status"] == C.PLANNED and row["lease_until"] is None
    assert row["last_error"].startswith("thin_fact_sheet: #1 'AI chip supply'")
    assert "only 1 usable facts" in row["last_error"] and "moved to #2 'Power for AI'" in row["last_error"]
    t = row["topic"]
    assert t["chosen_index"] == 1 and t["auto_picked"] is True
    assert t["owner_pick"] == 0, "the owner's own tap stays on record"
    assert [(d["index"], d["key"]) for d in t["build_drops"]] == [(0, "theme:ai-chips")]
    assert "only 1 usable facts" in t["build_drops"][0]["reason"]
    assert row["category"] == "battles" and row["subjects"] == [
        {"name": "NVIDIA CORP", "cik": CIK, "ticker": "NVDA"}]
    assert row["content_rejections"] == row["writer_failures"] == 0
    assert len(rec.notes) == 1 and "switched to #2" in rec.notes[0] and rec.terminal == []
    # The next tick builds the NEW candidate (its filings are fine).
    monkeypatch.setattr(sec_facts, "annual_facts", lambda *a, **k: _facts())
    monkeypatch.setattr(CL, "get_case_library", lambda path=None: LIBRARY)
    build(row)
    assert fw.calls[0]["topic_label"] == "Power for AI" and fw.calls[0]["category"] == "battles"
    assert stored(fake, piece["id"])["status"] == C.MEDIA_PENDING


async def _facts():
    return list(XBRL)


# ═════════════════════════════════════════════════════════════════════════════════════════════
# D. What is (not) built
# ═════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("tp", [None, topic(chosen=None)])
def test_a_piece_waiting_for_its_topic_is_not_touched(fake, rec, monkeypatch, tp):
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake, tp=tp)
    n = len(fake.calls)
    assert build(piece) is None
    assert fw.calls == [] and all(op != "update" for _t, op in fake.calls[n:])


@pytest.mark.parametrize("tp,why", [
    (topic(chosen=5), "outside the 2 candidates"),
    (topic(chosen=True), "not an integer"),
    (topic(cands=[candidate(category="growth")]), "category"),
    ({"version": 2, "chosen_index": 0, "candidates": []}, "version"),
    (topic(cands=[]), "no candidates"),
])
def test_an_unusable_topic_fails_loudly(fake, rec, monkeypatch, tp, why):
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))
    piece = make_piece(fake, tp=tp)
    build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.FAILED and why in row["last_error"]
    assert [t["reason"] for t in rec.terminal] == ["topic_invalid"]


@pytest.mark.parametrize("status", [C.TOPIC_PENDING, C.MEDIA_PENDING, C.IN_REVIEW, C.PUBLISHED,
                                    C.FAILED])
def test_other_statuses_are_not_built(fake, rec, monkeypatch, status):
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake, status=status)
    assert build(piece) is None and fw.calls == []


def test_past_the_cutoff_before_building_a_planned_piece_is_skipped(fake, rec, monkeypatch):
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    build(piece, now=B.catch_up_cutoff_for(piece))
    assert stored(fake, piece["id"])["status"] == C.SKIPPED and fw.calls == []
    assert [t["reason"] for t in rec.terminal] == ["catch_up_cutoff_passed"]


def test_a_naive_now_is_a_caller_bug(fake, rec):
    with pytest.raises(ValueError):
        build(make_piece(fake), now=datetime(2026, 10, 23, 12, 0))


# ═════════════════════════════════════════════════════════════════════════════════════════════
# E. Narration queueing
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_a_round_that_narrates_another_script_opens_the_next_round(fake, rec, monkeypatch):
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))
    rec.enqueue_results = [None]

    async def jobs(pid, *, sb=None):
        return [{"round": 1}, {"round": 2}]

    monkeypatch.setattr(ledger, "list_jobs", jobs)
    piece = make_piece(fake)
    build(piece)
    assert [q["round"] for q in rec.enqueued] == [1, 3]
    assert stored(fake, piece["id"])["status"] == C.MEDIA_PENDING


@pytest.mark.parametrize("results", [[None, None], [ValueError("block 3 has 80 words")],
                                     [ledger.LearnLedgerError("down")]])
def test_a_draft_whose_narration_cannot_be_queued_waits_drafted_and_alerts(fake, rec, monkeypatch,
                                                                           results):
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))
    rec.enqueue_results = list(results)

    async def jobs(pid, *, sb=None):
        return []

    monkeypatch.setattr(ledger, "list_jobs", jobs)
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.DRAFTED and row["last_error"].startswith("narration_enqueue_failed")
    assert row["narration_job_id"] is None and len(rec.notes) == 1 and "drafted" in rec.notes[0]
    # A later call for the drafted piece retries the queueing only.
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    build(row)
    row = stored(fake, piece["id"])
    assert row["status"] == C.MEDIA_PENDING and fw.calls == [] and row["last_error"] is None


def test_a_regenerated_piece_tells_the_writer_why_and_narrates_its_round(fake, rec, monkeypatch):
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake, review_round=2, reject_reason="Too dry; explain the power limits.")
    build(piece)
    assert fw.calls[0]["reject_reason"] == "Too dry; explain the power limits."
    assert rec.enqueued[0]["round"] == 2


# ═════════════════════════════════════════════════════════════════════════════════════════════
# F. Context: in-flight titles, progress, collisions
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_in_flight_titles_are_every_other_live_piece(fake, rec, monkeypatch):
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    make_piece(fake, key="2026-W41:deep_dive", status=C.IN_REVIEW, title="Other Piece",
               slug="other-piece-2026-10-09")
    make_piece(fake, key="2026-W40:deep_dive", status=C.EXPIRED, title="Old Piece",
               slug="old-piece-2026-10-02")
    piece = make_piece(fake, title="My Own Earlier Title")
    build(piece)
    kw = fw.calls[0]
    assert kw["in_flight_titles"] == ["Other Piece"]
    assert "other-piece-2026-10-09" in kw["served_slugs"]
    assert "old-piece-2026-10-02" not in kw["served_slugs"]


def test_progress_is_saved_under_its_key_and_resumed_by_the_next_generation(fake, rec, monkeypatch):
    payload = {"outline": {"title": "T"}, "sections": [[{"type": "paragraph", "text": "x"}]]}
    monkeypatch.setattr(W, "write_article", FakeWriter(failed(), progress=payload))
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    prog = row["progress"]
    assert prog["outline"] == {"title": "T"} and prog["v"] == B.PROGRESS_VERSION
    assert prog["review_round"] == 0 and len(prog["fact_sheet_sha256"]) == 64
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    build(row)
    assert fw.calls[0]["resume"] == {"outline": {"title": "T"}, "sections": payload["sections"]}


def test_resume_only_under_the_same_key():
    prog = {**B._progress_key("a" * 64, 1), "outline": {"t": 1}, "sections": []}
    assert B.resume_from(prog, "a" * 64, 1) == {"outline": {"t": 1}, "sections": []}
    assert B.resume_from(prog, "b" * 64, 1) is None              # the sheet changed
    assert B.resume_from(prog, "a" * 64, 2) is None              # a Regenerate
    assert B.resume_from({**prog, "prompt_version": "old"}, "a" * 64, 1) is None
    assert B.resume_from({}, "a" * 64, 1) is None and B.resume_from(None, "a" * 64, 1) is None


def test_a_slug_held_by_another_piece_is_a_content_rejection(fake, rec, monkeypatch):
    content = content_for()
    make_piece(fake, key="2026-W41:deep_dive", status=C.EXPIRED, slug=content["slug"])
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted(content)))
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.PLANNED and row["content_rejections"] == 1
    assert "slug_collision" in row["last_error"] and row["draft"] is None


# ═════════════════════════════════════════════════════════════════════════════════════════════
# G. The pure helpers
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_candidate_subjects_are_cleaned_and_capped(caplog):
    subs = B.candidate_subjects({"subjects": [
        {"name": "NVIDIA CORP", "cik": "1045810", "ticker": "nvda"},
        {"name": "NVIDIA CORP again", "cik": 1045810},                  # duplicate CIK
        {"name": "Private Lab", "cik": None, "ticker": None},
        {"name": "Bad CIK Co", "cik": "12345678901", "ticker": "$$$"},
        {"name": "  ", "cik": "1"}, "junk", {"cik": "2"},
        {"name": "Fifth", "cik": "5"},
    ]})
    assert subs == [C.Subject("NVIDIA CORP", CIK, "NVDA"), C.Subject("Private Lab", None, None),
                    C.Subject("Bad CIK Co", None, None)]
    assert "unusable CIK" in caplog.text and "keeping the first 3" in caplog.text


def test_candidate_peg_facts_keep_only_filed_spans():
    facts = B.candidate_peg_facts({"peg_facts": [
        vars(PEG), {**vars(PEG), "origin": "library"}, {"origin": "sec_peg", "text": 3}, "x",
        {**vars(PEG), "extra": "dropped"},
    ]})
    assert facts == [PEG, PEG]


def test_theme_and_label():
    assert B.candidate_theme(candidate()) == "ai-chips"
    assert B.candidate_theme(candidate(kind="ticker", key="NVDA")) is None
    assert B.candidate_label(candidate(label="  A   label ")) == "A label"
    assert B.candidate_label({"title": "", "key": "k"}) == "k"


def test_published_date_and_cutoff_read_the_frozen_row():
    piece = {"id": "x", "kind": "deep_dive", "period_key": KEY,
             "scheduled_publish_at": T0.isoformat(), "hold_until": HOLD.isoformat()}
    assert B.published_date_for(piece) == date(2026, 10, 23)
    assert B.published_date_for({**piece, "scheduled_publish_at": None}) == date(2026, 10, 23)
    assert B.published_date_for({"period_key": "junk"}) is None
    assert B.catch_up_cutoff_for(piece) == schedule_cutoff(piece)
    assert B.catch_up_cutoff_for({**piece, "hold_until": "not a date"}) is None


def test_assemble_fact_sheet_fails_closed_on_any_sec_error(rec, monkeypatch, caplog):
    async def config(cik, *, client=None):
        raise SECConfigException("SEC_EDGAR_USER_AGENT is blank")

    monkeypatch.setattr(sec_facts, "company_summary", config)
    with pytest.raises(B.FactSheetUnavailable) as e:
        asyncio.run(B.assemble_fact_sheet("deep_dive", candidate(), library=LIBRARY))
    assert "SECConfigException" in str(e.value) and "ERROR" in caplog.text


def test_assemble_fact_sheet_drops_a_subject_sec_does_not_know(rec, monkeypatch, caplog):
    async def unknown(cik, *, client=None):
        return None

    monkeypatch.setattr(sec_facts, "company_summary", unknown)
    with pytest.raises(B.FactSheetUnavailable, match="filings 0"):   # 2 primers: too thin
        asyncio.run(B.assemble_fact_sheet("deep_dive", candidate(), library=LIBRARY))
    monkeypatch.setattr(B, "MIN_SHEET_FACTS", 1)
    sheet = asyncio.run(B.assemble_fact_sheet("deep_dive", candidate(), library=LIBRARY))
    # No submissions record, so no filing date for the peg: it is dropped, never left undated.
    assert [f.id for f in sheet.facts] == ["L:dc-primer:f1", "L:nvda-primer:f1"]
    assert "[learn-sheet-peg-undated] P7" in caplog.text
    assert sheet.subjects == (C.Subject("NVIDIA CORP", CIK, "NVDA"),)
    assert sheet.theme_slug == "ai-chips" and sheet.topic_title == "AI chip supply"


def test_assemble_fact_sheet_dates_each_peg_from_its_own_8k(rec, monkeypatch, caplog):
    """The 3M week (2026-10-23): two pegs from two 8-Ks filed on different days, and one peg whose
    8-K is not in SEC's record. Each peg gets its OWN filing's date; the unknown one is dropped."""
    other = C.Fact("P8", "NVIDIA CORP entered into a lease for a data center campus.",
                   C.ORIGIN_SEC_PEG, "https://www.sec.gov/Archives/y", "0001045810-26-000099")
    stray = C.Fact("P9", "NVIDIA CORP opened an office.", C.ORIGIN_SEC_PEG,
                   "https://www.sec.gov/Archives/z", "0001045810-26-000042")

    async def summary(cik, *, client=None):
        return sec_facts.CompanySummary(
            name="NVIDIA CORP", cik=CIK, tickers=("NVDA",), exchanges=("Nasdaq",), sic="3674",
            sic_description=None, foreign_filer=False,
            recent_8k=(PEG_8K, sec_facts.Filing8K("2026-10-19", "0001045810-26-000099", "a.htm", ())))

    monkeypatch.setattr(sec_facts, "company_summary", summary)
    cand = candidate(peg_facts=[vars(PEG), vars(other), vars(stray)])
    sheet = asyncio.run(B.assemble_fact_sheet("deep_dive", cand, library=LIBRARY))
    pegs = [f for f in sheet.facts if f.origin == C.ORIGIN_SEC_PEG]
    assert [(f.id, f.accession) for f in pegs] == [("P1", PEG.accession),
                                                    ("P2", "0001045810-26-000099")]
    assert pegs[0].text == "Form 8-K filed with the SEC on October 21, 2026. " + PEG.text
    assert pegs[1].text == ("Form 8-K filed with the SEC on October 19, 2026. NVIDIA CORP "
                            "entered into a lease for a data center campus.")
    assert "[learn-sheet-peg-undated] P9" in caplog.text


def _multi(eid: str, themes, cik: Optional[str], kind: str, n: int) -> CL.LibraryEntry:
    """An entry with `n` distinct facts (f1..fn)."""
    base = _entry(eid, themes, cik, kind)
    src = base.facts[0].source
    facts = tuple(CL.LibraryFact(id=f"f{i}", text=f"{eid} fact number {i} explains one plain thing.",
                                 source=src) for i in range(1, n + 1))
    return CL.LibraryEntry(**{**vars(base), "facts": facts})


PRIORITY_LIBRARY = CL.CaseLibrary(version=1, entries=(
    _multi("theme-mech", ["ai-chips"], None, "mechanics", 6),            # the theme's mechanics
    _multi("other-co", ["ai-chips"], None, "company", 6),                # another company on the theme
    _multi("subject-mech", ["other-theme"], CIK, "mechanics", 6),        # about the subject, off theme
    _multi("subject-co", ["other-theme"], CIK, "company", 6),            # the subject's own company primer
    _multi("unrelated", ["other-theme"], None, "mechanics", 6),
))


def test_the_subjects_company_primer_leads_then_the_theme_then_the_rest():
    """2026-10-24: the library lists mechanics first and the cap drops from the end, so every
    company primer of a three-company Deep Dive's own subjects was cut and the writer described
    those companies from memory (the labelled previews' unsupported claims)."""
    entries = B._library_entries(PRIORITY_LIBRARY, "ai-chips", [CIK])
    assert [e.id for e in entries] == ["subject-co", "theme-mech", "subject-mech", "other-co"]
    facts = B._library_facts(PRIORITY_LIBRARY, entries)
    lead = [f.id for f in facts[:4 * B.PRIMER_LEAD_FACTS]]
    assert lead == [f"L:{e}:f{i}" for e in ("subject-co", "theme-mech", "subject-mech", "other-co")
                    for i in range(1, B.PRIMER_LEAD_FACTS + 1)]
    assert [f.id for f in facts[4 * B.PRIMER_LEAD_FACTS:]] == [
        f"L:{e}:f{i}" for e in ("subject-co", "theme-mech", "subject-mech", "other-co") for i in (5, 6)]


def test_the_cap_trims_every_primers_tail_before_it_drops_a_primer(rec, monkeypatch):
    async def summary(cik, *, client=None):
        return sec_facts.CompanySummary(
            name="NVIDIA CORP", cik=CIK, tickers=("NVDA",), exchanges=("Nasdaq",), sic="3674",
            sic_description=None, foreign_filer=False, recent_8k=(PEG_8K,))

    monkeypatch.setattr(sec_facts, "company_summary", summary)
    full = asyncio.run(B.assemble_fact_sheet("deep_dive", candidate(), library=PRIORITY_LIBRARY))
    lib_chars = sum(len(f.text) for f in full.facts if f.origin == C.ORIGIN_LIBRARY)
    # A cap that leaves room for the lead facts only: every primer keeps its first four.
    lead_chars = sum(len(f.text) for f in full.facts if f.origin == C.ORIGIN_LIBRARY
                     and int(f.id.rsplit(":f", 1)[1]) <= B.PRIMER_LEAD_FACTS)
    monkeypatch.setattr(fs, "DEEP_DIVE_SHEET_CAP", full.char_count() - lib_chars + lead_chars)
    capped = asyncio.run(B.assemble_fact_sheet("deep_dive", candidate(), library=PRIORITY_LIBRARY))
    kept = [f.id for f in capped.facts if f.origin == C.ORIGIN_LIBRARY]
    assert {i.split(":")[1] for i in kept} == {"subject-co", "theme-mech", "subject-mech", "other-co"}
    assert all(int(i.rsplit(":f", 1)[1]) <= B.PRIMER_LEAD_FACTS for i in kept)


def test_an_entry_with_fewer_facts_than_the_lead_gives_what_it_has():
    small = CL.CaseLibrary(version=1, entries=(_entry("one", ["ai-chips"], None),
                                               _multi("many", ["ai-chips"], None, "mechanics", 7)))
    facts = B._library_facts(small, B._library_entries(small, "ai-chips", []))
    assert [f.id for f in facts] == ["L:one:f1"] + [f"L:many:f{i}" for i in range(1, 5)] + \
        [f"L:many:f{i}" for i in (5, 6, 7)]
    assert B._library_facts(small, []) == []


def test_assemble_fact_sheet_never_swallows_a_cancellation(rec, monkeypatch):
    async def cancelled(cik, name, *, client=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr(sec_facts, "annual_facts", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(B.assemble_fact_sheet("deep_dive", candidate(), library=LIBRARY))


# ═════════════════════════════════════════════════════════════════════════════════════════════
# H. Review fixes (2026-10-23): the enqueue storm, the thin sheet, the title-uniqueness set
# ═════════════════════════════════════════════════════════════════════════════════════════════

REAL_ANNUAL_FACTS = sec_facts.annual_facts


def drafted_piece(fake, **fields: Any) -> Dict[str, Any]:
    """A piece already `drafted` (its narration not queued yet), as the scheduler re-offers it."""
    content = content_for()
    return make_piece(fake, status=C.DRAFTED, draft=content, draft_sha256=A.content_sha256(content),
                      title=content["title"], slug=content["slug"], **fields)


def fail_enqueue(rec, *errors: BaseException) -> None:
    rec.enqueue_results = list(errors)


@pytest.fixture
def no_jobs(monkeypatch):
    async def jobs(pid, *, sb=None):
        return []

    monkeypatch.setattr(ledger, "list_jobs", jobs)


# ── F5 / LW-CC-3: the engine comes from narration, never the raw setting ─────────────────────


def test_a_junk_engine_setting_never_reaches_the_enqueue(fake, rec, monkeypatch):
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))
    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", "Kokoro ")      # a Railway typo
    piece = make_piece(fake)
    build(piece)
    (q,) = rec.enqueued
    assert q["engine"] in C.ENGINES and q["engine"] == C.ENGINE_CHATTERBOX
    assert stored(fake, piece["id"])["status"] == C.MEDIA_PENDING


def test_the_build_cutoffs_use_the_engine_narration_will_use(fake, rec, monkeypatch):
    """Both catch-up decisions read `narration.current_engine()`, not the setting: here narration
    answers kokoro while the raw setting says chatterbox."""
    from app.services.learn_weekly import schedule
    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", C.ENGINE_CHATTERBOX)
    monkeypatch.setattr(rec, "current_engine", lambda fallback=None: C.ENGINE_KOKORO)
    piece = make_piece(fake)
    words = C.DEEP_DIVE_WORDS[1]
    assert B.catch_up_cutoff_for(piece) == schedule.catch_up_cutoff(HOLD, C.ENGINE_KOKORO, words)
    assert B.enqueue_cutoff_for(piece) == schedule.catch_up_cutoff(HOLD, C.ENGINE_KOKORO, words,
                                                                   build_seconds=0)
    assert B.enqueue_cutoff_for(piece) - B.catch_up_cutoff_for(piece) == timedelta(seconds=900)
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))
    build(piece)
    assert rec.enqueued[0]["engine"] == C.ENGINE_KOKORO


# ── F5 / LW-CC-3: a failing enqueue alerts once, is counted, and ends ────────────────────────


def test_the_same_enqueue_failure_alerts_once_then_fails_at_the_cap(fake, rec, monkeypatch, no_jobs):
    """Before the fix every 5-minute tick sent the same loud alert and the piece sat in drafted
    until its hold ended (~750 alerts)."""
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))
    fail_enqueue(rec, *[ValueError("unknown engine 'Kokoro '")] * B.MAX_ENQUEUE_ATTEMPTS)
    piece = make_piece(fake)
    build(piece)                                       # the draft is written; enqueue attempt 1
    row = stored(fake, piece["id"])
    assert row["status"] == C.DRAFTED and row["build_attempts"] == 1 and len(rec.notes) == 1
    assert "attempt 1/6" in rec.notes[0]
    for n in range(2, B.MAX_ENQUEUE_ATTEMPTS):         # the scheduler's next ticks
        build(stored(fake, piece["id"]))
        row = stored(fake, piece["id"])
        assert row["status"] == C.DRAFTED and row["build_attempts"] == n
    assert len(rec.notes) == 1, "the same stored reason is never alerted twice"
    assert rec.terminal == []
    build(stored(fake, piece["id"]))                   # attempt 6 = the cap
    row = stored(fake, piece["id"])
    assert row["status"] == C.FAILED and row["build_attempts"] == B.MAX_ENQUEUE_ATTEMPTS
    assert row["last_error"].startswith("narration_enqueue_exhausted (6/6 attempts): ValueError")
    assert rec.terminal == [{"id": piece["id"], "status": C.FAILED,
                             "reason": "narration_enqueue_failed", "stage": "drafted",
                             "row_status": C.FAILED}]
    assert len(rec.notes) == 1
    assert build(stored(fake, piece["id"])) is None and len(rec.enqueued) == B.MAX_ENQUEUE_ATTEMPTS


def test_a_changed_enqueue_failure_alerts_again(fake, rec, monkeypatch):
    piece = drafted_piece(fake)
    fail_enqueue(rec, ValueError("a"), ValueError("a"), ValueError("b"))
    for _ in range(3):
        build(stored(fake, piece["id"]))
    row = stored(fake, piece["id"])
    assert row["status"] == C.DRAFTED and row["build_attempts"] == 3
    assert len(rec.notes) == 2 and "(ValueError: b)" in rec.notes[1]


def test_past_the_narration_cutoff_one_failure_ends_the_piece(fake, rec, monkeypatch):
    piece = drafted_piece(fake)
    cutoff = B.enqueue_cutoff_for(piece)
    fail_enqueue(rec, ValueError("x"), ValueError("x"))
    build(stored(fake, piece["id"]), now=cutoff - timedelta(seconds=1))
    assert stored(fake, piece["id"])["status"] == C.DRAFTED and len(rec.notes) == 1
    build(stored(fake, piece["id"]), now=cutoff)
    row = stored(fake, piece["id"])
    assert row["status"] == C.FAILED and row["build_attempts"] == 2
    assert row["last_error"].startswith("narration_enqueue_exhausted (past the narration cut-off")
    assert [t["reason"] for t in rec.terminal] == ["narration_enqueue_failed"]
    assert len(rec.notes) == 1


def test_past_the_cutoff_a_successful_enqueue_still_goes_through(fake, rec, monkeypatch):
    """The cut-off ends a FAILING enqueue; a draft whose job is queued late still reaches review."""
    piece = drafted_piece(fake)
    build(stored(fake, piece["id"]), now=HOLD - timedelta(minutes=1))
    assert stored(fake, piece["id"])["status"] == C.MEDIA_PENDING and rec.terminal == []


def test_a_new_draft_starts_its_enqueue_count_afresh(fake, rec, monkeypatch, no_jobs):
    """Five failed enqueues of an EARLIER round's draft must not fail the next draft at its first."""
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))
    fail_enqueue(rec, ValueError("x"))
    piece = make_piece(fake, build_attempts=5)
    build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.DRAFTED and row["build_attempts"] == 1 and rec.terminal == []


def test_a_stale_retry_never_writes_the_count_backwards(fake, rec, monkeypatch):
    """A retry that read the piece at build_attempts 0 lands after two newer failures were counted:
    the compare-and-swap on the observed count refuses it, so the count (the cap's only input)
    never goes back to 1 — and the loser sends no alert of its own."""
    piece = drafted_piece(fake)
    stale = stored(fake, piece["id"])
    fail_enqueue(rec, ValueError("x"), ValueError("y"), ValueError("z"))
    build(stored(fake, piece["id"]))
    build(stored(fake, piece["id"]))
    assert stored(fake, piece["id"])["build_attempts"] == 2 and len(rec.notes) == 2
    out = asyncio.run(B.enqueue_narration(stale, stale["draft"], now=NOW))
    row = stored(fake, piece["id"])
    assert row["build_attempts"] == 2 and row["last_error"].endswith("ValueError: y")
    assert len(rec.notes) == 2 and out["build_attempts"] == 0, "the loser returns what it read"


def test_an_unstorable_enqueue_failure_is_alerted_once_per_process(fake, rec, monkeypatch):
    """The ledger refuses the write that records the failure: the stored value never changes, so
    the process remembers what it already said instead of alerting every tick."""
    piece = drafted_piece(fake)
    fail_enqueue(rec, ValueError("x"), ValueError("x"), ValueError("x"))
    for _ in range(3):
        fake.fail_next(ledger.PIECES, "update", RuntimeError("supabase 503"))
        build(stored(fake, piece["id"]))
    row = stored(fake, piece["id"])
    assert row["status"] == C.DRAFTED and row["build_attempts"] == 0 and row["last_error"] is None
    assert len(rec.notes) == 1
    # Queued at last: the memory is cleared, so a later failure of a NEW draft is alerted again.
    build(stored(fake, piece["id"]))
    assert stored(fake, piece["id"])["status"] == C.MEDIA_PENDING
    assert str(piece["id"]) not in B._ENQUEUE_ALERTED


def test_an_unreadable_attempt_count_still_counts_as_one(fake, rec, monkeypatch):
    piece = drafted_piece(fake)
    snapshot = {**stored(fake, piece["id"]), "build_attempts": True}     # a bool is not a count
    fail_enqueue(rec, ValueError("x"))
    asyncio.run(B.enqueue_narration(snapshot, snapshot["draft"], now=NOW))
    row = stored(fake, piece["id"])
    assert row["status"] == C.DRAFTED and row["build_attempts"] == 1 and len(rec.notes) == 1


# ── F3: a deterministic shortfall is told apart from an outage ────────────────────────────────


class FakeSEC:
    """The SEC client under the REAL `sec_facts.annual_facts`: `None` = 404 (the company never used
    the tag — an answer), an exception = a failed read."""

    def __init__(self, failing_tags=()):
        self.failing = set(failing_tags)
        self.calls: List[str] = []

    async def get_company_concept(self, cik, tag, taxonomy="us-gaap"):
        self.calls.append(tag)
        if tag in self.failing:
            raise SECUnavailableException("503 from SEC", status_code=503)
        return None


@pytest.fixture
def real_sec(monkeypatch, rec):
    monkeypatch.setattr(sec_facts, "annual_facts", REAL_ANNUAL_FACTS)
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "Caydex tests ops@example.com")
    sec_facts.clear_caches()
    client = FakeSEC()
    # The binding the build's recorder resolves the real client through (a module-level import).
    monkeypatch.setattr(B, "get_sec_edgar_client", lambda: client)
    yield client
    sec_facts.clear_caches()


def test_a_company_with_no_10k_rows_is_a_shortfall(fake, rec, real_sec, monkeypatch):
    """Every tag answers 404 (e.g. under a year past its IPO): deterministic — the topic moves on."""
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert real_sec.calls and row["topic"]["chosen_index"] == 1
    assert row["last_error"].startswith("thin_fact_sheet") and len(rec.notes) == 1


def test_a_thin_sheet_during_a_partial_sec_outage_retries_the_same_candidate(fake, rec, real_sec,
                                                                             monkeypatch):
    """One tag read fails: annual_facts drops that metric WITHOUT raising, so the thin sheet may be
    the outage's. It must retry (the cut-off decides), never give the topic away."""
    real_sec.failing = {"NetIncomeLoss"}
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.PLANNED and row["topic"]["chosen_index"] == 0
    assert "build_drops" not in row["topic"]
    assert row["last_error"].startswith("fact_sheet_unavailable: only 3 usable facts")
    assert "1 SEC reads failed (get_company_concept: SECUnavailableException)" in row["last_error"]
    assert rec.notes == [] and rec.terminal == []


def test_assemble_raises_a_shortfall_only_when_every_read_answered(rec, real_sec):
    with pytest.raises(B.FactSheetShortfall) as e:
        asyncio.run(B.assemble_fact_sheet("deep_dive", candidate(), library=LIBRARY))
    assert isinstance(e.value, B.FactSheetUnavailable), "the preview script catches the base class"
    assert e.value.usable == 3
    sec_facts.clear_caches()
    real_sec.failing = {"Revenues"}
    with pytest.raises(B.FactSheetUnavailable) as e2:
        asyncio.run(B.assemble_fact_sheet("deep_dive", candidate(), library=LIBRARY))
    assert not isinstance(e2.value, B.FactSheetShortfall)


def test_a_cached_answer_counts_as_every_read_answered(rec, real_sec):
    """sec_facts caches only a read where every tag answered; a second assembly reads nothing and
    must still see the shortfall."""
    with pytest.raises(B.FactSheetShortfall):
        asyncio.run(B.assemble_fact_sheet("deep_dive", candidate(), library=LIBRARY))
    n = len(real_sec.calls)
    with pytest.raises(B.FactSheetShortfall):
        asyncio.run(B.assemble_fact_sheet("deep_dive", candidate(), library=LIBRARY))
    assert len(real_sec.calls) == n, "served from the cache"


def thin(monkeypatch) -> None:
    async def none(cik, name, *, client=None):
        return []

    monkeypatch.setattr(sec_facts, "annual_facts", none)
    monkeypatch.setattr(CL, "get_case_library", lambda path=None: CL.CaseLibrary(1, ()))
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted()))


def test_a_thin_last_candidate_ends_skipped_with_one_alert(fake, rec, monkeypatch):
    thin(monkeypatch)
    piece = make_piece(fake, tp=topic(chosen=1))
    build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.SKIPPED and row["lease_until"] is None
    assert row["last_error"].startswith("thin_fact_sheet: #2 'Power for AI'")
    assert row["last_error"].endswith("no candidate left after #2")
    assert row["topic"]["chosen_index"] == 1 and row["topic"]["build_drops"][0]["index"] == 1
    assert [t["reason"] for t in rec.terminal] == ["thin_fact_sheet"] and rec.notes == []


def test_a_thin_sheet_skips_an_invalid_next_candidate(fake, rec, monkeypatch):
    thin(monkeypatch)
    cands = [candidate(), candidate(key="bad", category="growth"),
             candidate(key="power", label="Power for AI", category="battles")]
    piece = make_piece(fake, tp=topic(cands=cands))
    build(piece)
    t = stored(fake, piece["id"])["topic"]
    assert t["chosen_index"] == 2
    assert [d["index"] for d in t["build_drops"]] == [0, 1]
    assert t["build_drops"][1]["reason"].startswith("topic_invalid: candidate 1 category 'growth'")


def test_three_thin_candidates_alert_twice_then_end_once(fake, rec, monkeypatch):
    thin(monkeypatch)
    cands = [candidate(), candidate(key="b", label="B"), candidate(key="c", label="C")]
    piece = make_piece(fake, tp=topic(cands=cands))
    for _ in range(4):
        build(stored(fake, piece["id"]))
    row = stored(fake, piece["id"])
    assert row["status"] == C.SKIPPED and [d["index"] for d in row["topic"]["build_drops"]] == [0, 1, 2]
    assert len(rec.notes) == 2 and [t["reason"] for t in rec.terminal] == ["thin_fact_sheet"]


def test_past_the_cutoff_a_thin_sheet_skips_instead_of_switching(fake, rec, monkeypatch):
    thin(monkeypatch)
    piece = make_piece(fake)
    cutoff = B.catch_up_cutoff_for(piece)
    monkeypatch.setattr(B, "_utcnow", lambda: cutoff + timedelta(minutes=1))
    build(piece, now=cutoff - timedelta(minutes=1))
    row = stored(fake, piece["id"])
    assert row["status"] == C.SKIPPED and row["topic"]["chosen_index"] == 0
    assert row["last_error"].startswith("catch_up_cutoff_passed after thin_fact_sheet")
    assert [t["reason"] for t in rec.terminal] == ["catch_up_cutoff_passed"] and rec.notes == []


# ── data-migration F2: the title set comes from the table the unique index guards ────────────


def test_titles_held_back_by_the_archive_cap_are_still_taken(fake, rec, monkeypatch):
    """The served catalog keeps only the newest weekly pieces; the unique index covers every row.
    1,500 rows also proves the read pages past PostgREST's 1,000-row clamp."""
    rec.articles.rows = article_rows(1500)
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    build(make_piece(fake))
    kw = fw.calls[0]
    assert "Archived Piece 1499" in kw["served_titles"] and "Archived Piece 0" in kw["served_titles"]
    assert "archived-piece-1499-2024-01-01" in kw["served_slugs"]
    assert "The Fall of Sears" in kw["served_titles"], "the served catalog's titles stay"
    assert kw["served_titles"].index("The Fall of Sears") < kw["served_titles"].index("Archived Piece 0")
    assert rec.articles.selects and set(rec.articles.selects) == {"title,slug"}
    assert rec.articles.ranges[:2] == [(0, 999), (1000, 1999)]
    assert kw["catalog"] and all("Archived Piece" not in a.get("title", "") for a in kw["catalog"]), \
        "the served catalog still feeds the related tiles"


def test_a_title_in_both_lists_is_offered_once(fake, rec, monkeypatch):
    rec.articles.rows = [{"id": "1", "title": "the fall of  SEARS", "slug": "the-fall-of-sears"}]
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    build(make_piece(fake))
    titles = fw.calls[0]["served_titles"]
    assert sum(1 for t in titles if B._title_fold(t) == "the fall of sears") == 1


def test_an_unreadable_article_table_waits_rather_than_failing_open(fake, rec, monkeypatch):
    rec.articles.fail = RuntimeError("PGRST 503")
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert fw.calls == [] and row["status"] == C.PLANNED
    assert row["last_error"].startswith("catalog_unavailable: the money_move_articles title read failed")
    assert row["content_rejections"] == row["writer_failures"] == 0


def test_in_flight_statuses_are_the_build_queue_through_the_publish():
    assert B.IN_FLIGHT_STATUSES == (C.PLANNED, C.TOPIC_PENDING, C.BUILDING, C.DRAFTED,
                                    C.MEDIA_PENDING, C.READY_FOR_REVIEW, C.IN_REVIEW, C.APPROVED,
                                    C.PUBLISHING)


def test_years_of_published_pieces_never_starve_this_weeks_in_flight_titles(fake, rec, monkeypatch):
    """Before: one oldest-first read of every non-terminal piece (published included) capped at
    200 — after ~4 years of Deep Dives this week's other pieces fell off the end."""
    start = date(2025, 1, 1)
    for i in range(201):
        day = start + timedelta(days=i)
        at = datetime(day.year, day.month, day.day, 17, 0, tzinfo=UTC)
        row = ledger.create_piece_sync(f"{day.isoformat()}:slot", "slot", scheduled_publish_at=at,
                                       hold_until=at + timedelta(hours=48), dry_run=False, sb=fake)
        r = fake.row(ledger.PIECES, row["id"])
        r.update(status=C.PUBLISHED, title=f"Old Published {i}", slug=f"old-published-{i}")
    make_piece(fake, key="2026-W44:deep_dive", status=C.IN_REVIEW, title="Next Week's Piece",
               slug="next-weeks-piece-2026-10-30")
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    build(make_piece(fake))
    kw = fw.calls[0]
    assert kw["in_flight_titles"] == ["Next Week's Piece"]
    assert "next-weeks-piece-2026-10-30" in kw["served_slugs"]


def test_an_in_flight_read_that_fills_its_limit_fails_closed(fake, rec, monkeypatch):
    monkeypatch.setattr(B, "_IN_FLIGHT_LIMIT", 2)
    make_piece(fake, key="2026-W44:deep_dive", status=C.IN_REVIEW, title="A", slug="a-2026-10-30")
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert fw.calls == [] and row["last_error"].startswith("catalog_unavailable: the in-flight")


def test_build_drops_stay_bounded(fake, rec, monkeypatch):
    thin(monkeypatch)
    tp = topic()
    tp["build_drops"] = [{"index": 0, "key": f"old:{i}", "reason": "earlier"} for i in range(25)]
    piece = make_piece(fake, tp=tp)
    build(piece)
    drops = stored(fake, piece["id"])["topic"]["build_drops"]
    assert len(drops) == B._BUILD_DROPS_MAX and drops[-1]["key"] == "theme:ai-chips"


def test_the_sec_recorder_records_failures_but_never_a_cancellation(monkeypatch):
    class Client:
        user_agent = "ua"

        async def get_company_concept(self, cik, tag, taxonomy="us-gaap"):
            if tag == "cancel":
                raise asyncio.CancelledError()
            raise SECUnavailableException("503")

    created: List[Client] = []
    monkeypatch.setattr(B, "get_sec_edgar_client", lambda: created.append(Client()) or created[-1])
    sec = B._SECReadRecorder()
    assert created == [], "the real client is created only when a read happens"
    assert sec.user_agent == "ua", "plain attributes pass through"
    with pytest.raises(SECUnavailableException):
        asyncio.run(sec.get_company_concept("1", "Revenues"))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sec.get_company_concept("1", "cancel"))
    assert sec.reads == 2 and sec.failures == ["get_company_concept: SECUnavailableException"]
    assert len(created) == 1
    import copy as _copy
    clone = _copy.copy(B._SECReadRecorder.__new__(B._SECReadRecorder))
    with pytest.raises(AttributeError):
        clone._client                                    # no infinite recursion


# ═════════════════════════════════════════════════════════════════════════════════════════════
# I. Cover art (owner 2026-11-01, `cover_art.py`): between the accepted draft and its write —
#    never a failed or delayed piece
# ═════════════════════════════════════════════════════════════════════════════════════════════

ART = {"imageUrl": "https://x.supabase.co/storage/v1/object/public/money-moves-images/articles/a.hero.jpg",
       "imageCardUrl": "https://x.supabase.co/storage/v1/object/public/money-moves-images/articles/a.card.jpg"}


class ArtStub:
    """Stands in for `cover_art.cover_art_for`; records what it saw (the lease too)."""

    def __init__(self, fake, answer=None, exc: Optional[BaseException] = None) -> None:
        self.fake, self.answer, self.exc = fake, answer, exc
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, draft, *, subjects=(), piece_id=None, previous_validation=None, **_):
        row = self.fake.row(ledger.PIECES, piece_id)
        self.calls.append({"draft": dict(draft), "subjects": list(subjects), "piece_id": piece_id,
                           "lease_until": row.get("lease_until"), "status": row.get("status")})
        if self.exc is not None:
            raise self.exc
        return self.answer


def _art_module(monkeypatch):
    from app.services.learn_weekly import cover_art

    monkeypatch.setattr(B, "_cover_art", lambda: cover_art)
    return cover_art


def test_art_off_never_calls_the_art_step_and_records_it(fake, rec, monkeypatch):
    cover_art = _art_module(monkeypatch)
    stub = ArtStub(fake, exc=AssertionError("must not be called while LEARN_ART_ENABLED is off"))
    monkeypatch.setattr(cover_art, "cover_art_for", stub)
    monkeypatch.setattr(settings, "LEARN_ART_ENABLED", False)
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert stub.calls == [] and row["status"] == C.MEDIA_PENDING
    assert "imageUrl" not in row["draft"] and row["draft"] == fw.outcomes[0].content
    assert row["validation"]["cover_art"] == {"status": "off"}


def test_cover_art_joins_the_draft_before_its_hash_and_its_narration(fake, rec, monkeypatch):
    cover_art = _art_module(monkeypatch)
    record = {"status": "ok", "reused": False, "subject": "a wafer", "treatment": "duotone_press",
              "model": "image-model", "prompt_sha1": "abcdef012345", "imageUrl": ART["imageUrl"]}
    stub = ArtStub(fake, answer=(dict(ART), record))
    monkeypatch.setattr(cover_art, "cover_art_for", stub)
    monkeypatch.setattr(settings, "LEARN_ART_ENABLED", True)
    content = content_for()
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted(content)))
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.MEDIA_PENDING
    (call,) = stub.calls
    # the art step saw the FINAL accepted draft (disclosure appended) while this generation held a live lease
    assert call["draft"] == content and call["draft"]["sections"][-1]["content"][-1] == A.disclosure_block()
    assert call["lease_until"] is not None and call["status"] == C.BUILDING
    assert call["subjects"] and call["subjects"][0].name == "NVIDIA CORP"
    assert row["draft"] == {**content, **ART}
    assert row["draft_sha256"] == A.content_sha256({**content, **ART}), "the fence sees the final draft"
    (q,) = rec.enqueued
    assert q["content"] == {**content, **ART}
    assert row["validation"]["cover_art"] == record and row["validation"]["outcome"] == "accepted"
    assert "coverArt" not in row["draft"] and "model" not in row["draft"], "the record never reaches the app"


def test_a_failed_art_step_leaves_the_piece_artless_and_on_time(fake, rec, monkeypatch):
    cover_art = _art_module(monkeypatch)
    stub = ArtStub(fake, answer=(None, {"status": "failed", "error": "CoverArtImageError: refused"}))
    monkeypatch.setattr(cover_art, "cover_art_for", stub)
    monkeypatch.setattr(settings, "LEARN_ART_ENABLED", True)
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.MEDIA_PENDING and row["draft"] == fw.outcomes[0].content
    assert row["validation"]["cover_art"]["status"] == "failed" and rec.terminal == []


@pytest.mark.parametrize("failure", ["hang", "bug"])
def test_the_real_art_door_never_fails_or_stalls_the_build(fake, rec, monkeypatch, caplog, failure):
    """The REAL `cover_art_for` around a step that hangs past LEARN_ART_TIMEOUT_SECONDS, or raises a
    bug: the piece is drafted artless and narrated, with a WARNING naming the piece."""
    cover_art = _art_module(monkeypatch)

    async def step(*a, **k):
        if failure == "hang":
            await asyncio.sleep(30)
        raise RuntimeError("art bug")

    monkeypatch.setattr(cover_art, "make_cover_art", step)
    monkeypatch.setattr(settings, "LEARN_ART_ENABLED", True)
    monkeypatch.setattr(settings, "LEARN_ART_TIMEOUT_SECONDS", 0.05)
    fw = FakeWriter(accepted())
    monkeypatch.setattr(W, "write_article", fw)
    piece = make_piece(fake)
    with caplog.at_level(logging.WARNING):
        build(piece)
    row = stored(fake, piece["id"])
    assert row["status"] == C.MEDIA_PENDING and "imageUrl" not in row["draft"]
    assert row["validation"]["cover_art"]["status"] == "failed"
    assert any(str(piece["id"]) in r.getMessage() and "without cover art" in r.getMessage()
               for r in caplog.records)
    assert len(rec.enqueued) == 1


def test_no_paid_art_for_a_piece_whose_lease_is_gone(fake, rec, monkeypatch):
    cover_art = _art_module(monkeypatch)
    stub = ArtStub(fake, exc=AssertionError("must not draw for a lost lease"))
    monkeypatch.setattr(cover_art, "cover_art_for", stub)
    monkeypatch.setattr(settings, "LEARN_ART_ENABLED", True)
    monkeypatch.setattr(W, "write_article", FakeWriter(accepted(), before_calls=0))

    async def lost(*a, **k):
        return None

    monkeypatch.setattr(ledger, "refresh_lease", lost)
    piece = make_piece(fake)
    build(piece)
    row = stored(fake, piece["id"])
    assert stub.calls == []
    assert row["validation"]["cover_art"] == {"status": "skipped", "error": "lease lost before the art step"}
