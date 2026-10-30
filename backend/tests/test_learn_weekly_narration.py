"""The narration of a Wiser weekly piece (`app/services/learn_weekly/narration.py`, plan §1.8).

Hermetic: the flow tests run the REAL ledger against the in-memory PostgREST fake of
`test_learn_weekly_ledger.py` (which enforces migration 204's keys and CHECKs), plus an in-memory
Storage bucket. Nothing reaches Supabase, Storage or the network.

What is pinned, and why it matters:
  * the narration ORDER equals `scripts/clone_learn_audio.narration_blocks` (AST-extracted — the
    script imports torch, so it is never imported) over every bundled article: a weekly piece is
    voiced the way the whole catalog was, and the read-along keys line up;
  * the sentence split equals `scripts/_forced_align.split_sentences` (pattern AND behaviour), and
    the catalog's own read-along agrees with it — the worker's sentence texts are compared to it;
  * the timing gate refuses every malformed table with a precise reason, and the seconds-per-word
    band refuses a DROPPED sentence (the one defect forced alignment hides) while passing all 854
    sentences of the catalog's real narration;
  * the merge writes exactly the aligner's shape (readAlong / itemsReadAlong / duration / flag);
  * claim / heartbeat / upload / complete / fail move the job and the piece only under the caller's
    claim, verify the object like marketing's `_verify_object`, answer a replay or a cancelled job
    with nothing written, and fail a piece (with the owner alert) only past the cut-off.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import math
import re
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import test_learn_weekly_ledger as ledger_fake
from app.config import settings
from app.services import learn_audio_urls
from app.services.learn_weekly import contracts as c
from app.services.learn_weekly import ledger, narration, schedule
from app.services.learn_weekly.errors import (
    LearnNarratorAssetMismatch,
    LearnNarratorAssetMissing,
    LearnNarratorJobNotHeld,
    LearnNarratorLedgerError,
    LearnNarratorNotFound,
    LearnNarratorRequestInvalid,
)
from app.services.learn_weekly.narration import WorkerClaim

_BACKEND = Path(__file__).resolve().parents[1]
_CLONE_SCRIPT = _BACKEND / "scripts" / "clone_learn_audio.py"
_ALIGN_SCRIPT = _BACKEND / "scripts" / "_forced_align.py"
_BUNDLES = [_BACKEND / "data" / "money_moves.json",
            _BACKEND.parent / "frontend" / "ios" / "ios" / "Resources" / "MoneyMoves" / "money_moves.json"]

UTC = timezone.utc
PUBLISH = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)          # Fri 2026-10-23 23:00 ET
HOLD = PUBLISH + timedelta(hours=48)
NOW = datetime(2026, 10, 23, 13, 0, tzinfo=UTC)             # Fri 09:00 ET: well inside the cut-off
KEY = "2026-W43:deep_dive"
SLUG = "how-ai-data-centres-get-built-2026-10-23"
SUPABASE_URL = "https://example-project.supabase.co"


# ── the scripts, AST-extracted (never imported: clone_learn_audio imports torch at top) ─────────


def _extract(path: Path, functions: tuple, assigns: tuple = ()) -> Dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in assigns for t in node.targets):
            body.append(node)
    found = {n.name for n in body if isinstance(n, ast.FunctionDef)} | {
        t.id for n in body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    assert found == set(functions) | set(assigns), f"{path.name}: could not find {set(functions) | set(assigns) - found}"
    ns: Dict[str, Any] = {"re": re}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), ns)
    return ns


CLONE = _extract(_CLONE_SCRIPT, ("strip_markup", "narration_blocks"))
ALIGN = _extract(_ALIGN_SCRIPT, ("strip_markup", "split_sentences"), ("_SENT",))


def _articles() -> List[Dict[str, Any]]:
    out = []
    for path in _BUNDLES:
        if path.exists():
            out += json.loads(path.read_text(encoding="utf-8"))["articles"]
    return out


def _all_block_texts() -> List[str]:
    texts = []
    for a in _articles():
        texts += [b.text for b in narration.narration_blocks(a)]
    return texts


# ── a small iOS-shaped article (every narrated block type, an empty bullet item) ──────────────

ARTICLE: Dict[str, Any] = {
    "slug": SLUG,
    "title": "How AI Data Centres Get Built",
    "subtitle": "Power, chips and concrete. Why the build-out takes years.",
    "category": "blueprints",
    "isFeatured": True,
    "hasAudioVersion": False,
    "audioUrl": None,
    "sections": [
        {
            "title": "The Peg",
            "icon": "chart.bar.fill",
            "content": [
                {"type": "paragraph",
                 "text": "In its quarterly filing the company reported **record** capital spending. "
                         "Most of it went to new data centres. The rest bought networking gear."},
                {"type": "subheading", "text": "Why it matters"},
                {"type": "callout", "style": "info", "text": "A data centre is a power plant with servers attached."},
            ],
        },
        {
            "title": "How the build works",
            "icon": "building.2.fill",
            "content": [
                {"type": "bulletList", "items": ["Secure the land and the grid connection first.", "",
                                                 "Order chips a year ahead. Lead times are long."]},
                {"type": "paragraph", "text": "Mark-to-market accounting is not involved here."},
            ],
        },
    ],
}


# ── fakes ─────────────────────────────────────────────────────────────────────────────────────


class FakeBucket:
    def __init__(self, storage: "FakeStorage", name: str):
        self.storage, self.name = storage, name

    def _maybe_fail(self, op: str) -> None:
        exc = self.storage.failures.pop(op, None)
        if exc is not None:
            raise exc

    def list(self, prefix: str, options: Dict[str, Any]):
        self._maybe_fail("list")
        self.storage.calls.append(("list", self.name, prefix))
        search = options.get("search", "")
        out = []
        for (bucket, path), meta in self.storage.objects.items():
            head, _, name = path.rpartition("/")
            if bucket == self.name and head == prefix and name.startswith(search):
                out.append({"name": name, "metadata": dict(meta)})
        return out[: options.get("limit", 100)]

    def remove(self, paths: List[str]):
        self._maybe_fail("remove")
        self.storage.calls.append(("remove", self.name, tuple(paths)))
        for p in paths:
            self.storage.objects.pop((self.name, p), None)
        return []

    def create_signed_upload_url(self, path: str):
        self._maybe_fail("sign")
        self.storage.calls.append(("sign", self.name, path))
        return {"signed_url": f"https://fake.storage/upload/sign/{self.name}/{path}?token=SECRET-TOKEN",
                "token": "SECRET-TOKEN", "path": path}


class FakeStorage:
    def __init__(self):
        self.objects: Dict[tuple, Dict[str, Any]] = {}
        self.calls: List[tuple] = []
        self.failures: Dict[str, Exception] = {}

    def from_(self, bucket: str) -> FakeBucket:
        return FakeBucket(self, bucket)

    def put(self, path: str, size: Optional[int], mimetype: Optional[str] = "audio/mp4",
            bucket: str = narration.AUDIO_BUCKET) -> None:
        self.objects[(bucket, path)] = {"size": size, "mimetype": mimetype}


class FakeClient(ledger_fake.FakeSupabase):
    def __init__(self):
        super().__init__()
        self.storage = FakeStorage()


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    # Both bindings the code under test uses: the ledger's own and narration's (Storage).
    monkeypatch.setattr(ledger, "get_supabase", lambda: client)
    monkeypatch.setattr(narration, "get_supabase", lambda: client)
    monkeypatch.setattr(settings, "SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", "chatterbox")
    return client


@pytest.fixture
def alerts(monkeypatch):
    """The review builder's `alerts` module, replaced in sys.modules (narration imports it at call
    time with importlib, which reads sys.modules first)."""
    mod = types.ModuleType("app.services.learn_weekly.alerts")
    mod.calls = []

    async def notify_terminal(piece, *, status, reason, stage=None):
        mod.calls.append({"piece_id": piece.get("id"), "status": status, "reason": reason, "stage": stage})

    mod.notify_terminal = notify_terminal
    monkeypatch.setitem(sys.modules, "app.services.learn_weekly.alerts", mod)
    return mod


def _media_pending(fake, *, content=None, engine="chatterbox", hold=HOLD, slug=SLUG, dry_run=True):
    """A piece in media_pending on round 1 of `content`, as the build step leaves it."""
    content = copy.deepcopy(content or ARTICLE)
    piece = ledger.create_piece_sync(KEY, c.KIND_DEEP_DIVE, scheduled_publish_at=PUBLISH, hold_until=hold,
                                     dry_run=dry_run, sb=fake)
    piece = ledger.transition_sync(piece["id"], from_statuses=(c.PLANNED,), to_status=c.DRAFTED,
                                   fields={"slug": slug, "title": content["title"], "draft": content,
                                           "draft_sha256": narration.draft_sha256(content)}, sb=fake)
    job = _enqueue_sync(fake, piece, content, engine=engine, round=1)
    piece = ledger.transition_sync(piece["id"], from_statuses=(c.DRAFTED,), to_status=c.MEDIA_PENDING,
                                   fields={"narration_job_id": job["id"]}, sb=fake)
    return piece, job


def _enqueue_sync(fake, piece, content, *, engine="chatterbox", round=1):
    """`enqueue_for_piece`'s exact ledger call, synchronously (an async test's setup cannot
    asyncio.run). `test_enqueue_for_piece_writes_what_the_sync_setup_writes` pins the two equal."""
    blocks = narration.narration_blocks(content)
    job = ledger.enqueue_job_sync(piece["id"], round=round, script=narration.script_json(blocks),
                                  script_sha256=narration.script_sha256(blocks), engine=engine,
                                  voice=narration.voice_key(narration.voice_spec(engine)),
                                  pipeline_version=narration.PIPELINE_VERSION, sb=fake)
    assert job is not None
    return job


def _timings(blocks, *, spw=0.36, start=0.4, gap=0.6, pause=0.2):
    """A valid timing table for `blocks` (sentences at `spw` s per spoken word) and its end time."""
    t, out = start, []
    for b in blocks:
        b0, sents = t, []
        for s in narration.sentence_split(b.text):
            d = narration.spoken_words(s) * spw
            sents.append({"text": s, "start": round(t, 3), "end": round(t + d, 3)})
            t += d + pause
        out.append({"i": b.i, "start": round(b0, 3), "end": round(t - pause, 3), "sentences": sents})
        t += gap
    return out, round(t, 3)


async def _claimed(fake, engines=("chatterbox",), now=NOW):
    job = await narration.claim_for_worker(list(engines), worker_version="test/1", now=now)
    assert job is not None
    return job, WorkerClaim(job["attempts"], job["claim_nonce"])


def _blocks(content=None):
    return narration.narration_blocks(content or ARTICLE)


def _path(sha="ab" * 32):
    return narration.weekly_audio_path(SLUG, sha)


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 1. The script: order, split, markup — pinned to the scripts that voiced the catalog
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_the_bundles_are_present_and_hold_23_articles_each():
    """Anti-vacuity for the parity tests below: they iterate these."""
    for path in _BUNDLES[:1]:
        assert len(json.loads(path.read_text(encoding="utf-8"))["articles"]) == 23
    assert len(_articles()) >= 23


def test_narration_order_equals_the_clone_script_over_every_bundled_article():
    checked = 0
    for article in _articles():
        ours = narration.narration_blocks(article)
        assert [b.text for b in ours] == CLONE["narration_blocks"](article), article["slug"]
        assert [b.i for b in ours] == list(range(len(ours)))
        checked += len(ours)
    assert checked > 800


def test_every_block_dest_points_at_its_own_text_and_role():
    for article in _articles() + [ARTICLE]:
        for b in narration.narration_blocks(article):
            kind, idx = b.dest[0], b.dest[1:]
            if kind in ("title", "subtitle"):
                src, role = article[kind], c.ROLE_CONTEXT
            elif kind == "section_title":
                src, role = article["sections"][idx[0]]["title"], c.ROLE_CONTEXT
            elif kind == "block":
                src, role = article["sections"][idx[0]]["content"][idx[1]]["text"], c.ROLE_BODY
            else:
                src, role = article["sections"][idx[0]]["content"][idx[1]]["items"][idx[2]], c.ROLE_ITEM
            assert narration.strip_markup(src) == b.text and b.role == role, (article.get("slug"), b)


def test_the_wire_view_never_carries_dest():
    for b in _blocks():
        assert set(b.wire()) == {"i", "text", "role"}


def test_an_empty_bullet_item_is_skipped_but_keeps_its_index():
    items = [b for b in _blocks() if b.role == c.ROLE_ITEM]
    assert [b.dest[3] for b in items] == [0, 2]


def test_markup_is_stripped_like_the_scripts():
    para = next(b for b in _blocks() if b.dest == ("block", 0, 0))
    assert "**" not in para.text and "record capital" in para.text
    for s in ["**a** b", "", "no markup", "***x***", "a ** b"]:
        assert narration.strip_markup(s) == CLONE["strip_markup"](s) == ALIGN["strip_markup"](s)


def test_the_sentence_pattern_is_the_aligners_byte_for_byte():
    assert narration._SENT.pattern == ALIGN["_SENT"].pattern


_SPLIT_CASES = [
    "", "   ", "One sentence.", "Two. Sentences.", "U.S. Steel rose. Then it fell.",
    "He said “Yes.” Then left.", 'She said "No." Then "maybe."', "Revenue was $4.5 billion. Margins grew.",
    "Wait... what? Really! 2019 was odd.", "ends without a terminator", "line one.\nLine two.",
    "e.g. this is lower-case. So is this.", "Q3 2026. 10-K filed. ok.", "**Bold.** Next one.",
    "A sentence?  Two spaces before this.", "Quote: ‘single.’ Then more.",
]


@pytest.mark.parametrize("text", _SPLIT_CASES)
def test_the_sentence_split_equals_the_aligners(text):
    assert narration.sentence_split(text) == ALIGN["split_sentences"](ALIGN["strip_markup"](text))


def test_the_sentence_split_equals_the_aligners_over_every_bundled_block():
    texts = _all_block_texts()
    assert len(texts) > 800
    for t in texts:
        assert narration.sentence_split(t) == ALIGN["split_sentences"](ALIGN["strip_markup"](t)), t


def test_the_catalogs_own_read_along_agrees_with_the_server_split():
    """The bundled readAlong was written by the aligner from these texts — the server split must
    reproduce its sentence texts exactly, or a worker following the same split would be refused."""
    n = 0
    for a in _articles():
        for s in a["sections"]:
            for b in s["content"]:
                if "readAlong" in b:
                    assert [x["text"] for x in b["readAlong"]] == narration.sentence_split(b["text"])
                    n += 1
                for item, spans in zip(b.get("items") or [], b.get("itemsReadAlong") or []):
                    assert [x["text"] for x in spans] == narration.sentence_split(item)
                    n += 1
    assert n > 300


def test_the_catalogs_own_narration_passes_the_band():
    """Calibration: every sentence of the real, aligned catalog narration sits inside SPW_BAND, so
    the band refuses outliers, not the house voice."""
    lo, hi = narration.SPW_BAND
    seen = 0
    for a in _articles():
        for s in a["sections"]:
            for b in s["content"]:
                for spans in [b.get("readAlong") or []] + list(b.get("itemsReadAlong") or []):
                    for x in spans:
                        spw = (x["end"] - x["start"]) / narration.spoken_words(x["text"])
                        assert lo <= spw <= hi, (a["slug"], x)
                        seen += 1
    assert seen > 800


def test_the_catalogs_pauses_fit_under_the_silence_cap():
    """Calibration of MAX_SILENCE_SECONDS: no pause between two sentences of one catalog block comes
    near it (between blocks the catalog's table omits the spoken context, so it is not comparable)."""
    longest = 0.0
    for a in _articles():
        for s in a["sections"]:
            for b in s["content"]:
                for spans in [b.get("readAlong") or []] + list(b.get("itemsReadAlong") or []):
                    for x, y in zip(spans, spans[1:]):
                        longest = max(longest, y["start"] - x["end"])
    assert 1.0 < longest < narration.MAX_SILENCE_SECONDS / 2


def test_unwritten_audio_between_or_after_the_sentences_is_refused():
    blocks = _blocks()
    t, end = _timings(blocks)
    # a hallucinated 12 s run-on inside the callout block's window, after its sentence
    k = next(e for e, b in zip(t, blocks) if b.dest == ("block", 0, 2))
    shift = 12.0
    k["end"] += shift
    for later in t[t.index(k) + 1:]:
        later["start"] += shift
        later["end"] += shift
        for s in later["sentences"]:
            s["start"] += shift
            s["end"] += shift
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        narration.validate_timings(blocks, t, duration_seconds=end + shift)
    assert "covered by no sentence" in str(e.value)
    t2, end2 = _timings(blocks)
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        narration.validate_timings(blocks, t2, duration_seconds=end2 + 7.0)
    assert "past the last sentence" in str(e.value)
    t3, end3 = _timings(blocks, start=6.5)        # 6.5 s of audio before the title is read
    with pytest.raises(LearnNarratorRequestInvalid):
        narration.validate_timings(blocks, t3, duration_seconds=end3)
    narration.validate_timings(blocks, *_timings(blocks, start=5.5)[:1], duration_seconds=_timings(blocks, start=5.5)[1])


def test_spoken_words_counts_compound_parts_and_never_zero():
    assert narration.spoken_words("Mark-to-Market") == 3
    assert narration.spoken_words("don't stop") == 2
    assert narration.spoken_words("—") == 1
    assert narration.spoken_words("") == 1


@pytest.mark.parametrize("bad", [
    {"title": 7}, {"subtitle": ["x"]}, {"sections": "nope"}, {"sections": [3]},
    {"sections": [{"content": [{"type": "paragraph", "text": {"x": 1}}]}]},
    {"sections": [{"content": [{"type": "bulletList", "items": "a,b"}]}]},
    {"sections": [{"content": [{"type": "bulletList", "items": ["ok", 5]}]}]},
    {"sections": [{"title": 1.5}]},
])
def test_a_malformed_draft_is_a_value_error_not_a_crash(bad):
    with pytest.raises(ValueError):
        narration.narration_blocks(bad)


def test_unknown_block_types_and_falsy_texts_are_skipped_like_the_script():
    article = {"title": "", "sections": [{"title": None, "content": [
        {"type": "image", "text": "not narrated"}, {"type": "paragraph", "text": ""},
        {"type": "quote", "text": "A quote."}]}]}
    assert [b.text for b in narration.narration_blocks(article)] == CLONE["narration_blocks"](article) == ["A quote."]


# ── hashes, script round trip ───────────────────────────────────────────────────────────────


def test_script_sha_covers_dest_not_just_the_words():
    moved = copy.deepcopy(ARTICLE)
    moved["sections"][0]["content"].insert(0, {"type": "image"})        # same words, other indices
    assert [b.text for b in _blocks(moved)] == [b.text for b in _blocks()]
    assert narration.script_sha256(_blocks(moved)) != narration.script_sha256(_blocks())


def test_the_stored_script_round_trips_and_a_tampered_one_is_refused():
    blocks = _blocks()
    stored = json.loads(json.dumps(narration.script_json(blocks)))
    sha = narration.script_sha256(blocks)
    assert narration.blocks_from_script(stored, expected_sha256=sha) == blocks
    tampered = copy.deepcopy(stored)
    tampered[1]["text"] = "Something else."
    with pytest.raises(ValueError):
        narration.blocks_from_script(tampered, expected_sha256=sha)
    for bad in ([], None, [{"i": 1, "text": "x", "role": "body", "dest": ["title"]}],
                [{"i": 0, "text": " ", "role": "body", "dest": ["title"]}],
                [{"i": 0, "text": "x", "role": "narrator", "dest": ["title"]}],
                [{"i": 0, "text": "x", "role": "body", "dest": ["block", 1]}],
                [{"i": 0, "text": "x", "role": "body", "dest": ["block", 1, True]}]):
        with pytest.raises(ValueError):
            narration.blocks_from_script(bad)


def test_draft_sha_is_canonical_and_refuses_nan():
    a = {"b": 1, "a": [1, 2], "é": "ü"}
    assert narration.draft_sha256(a) == narration.draft_sha256(dict(reversed(list(a.items()))))
    with pytest.raises(ValueError):
        narration.draft_sha256({"x": float("nan")})


# ── voice and paths ─────────────────────────────────────────────────────────────────────────


def test_the_chatterbox_voice_is_the_catalog_settings_and_fits_the_column():
    spec = narration.voice_spec(c.ENGINE_CHATTERBOX)
    clone = _extract(_CLONE_SCRIPT, (), ("EXAG", "CFG", "TARGET_WPM"))
    assert spec == {"exaggeration": clone["EXAG"], "cfg_weight": clone["CFG"],
                    "target_wpm": clone["TARGET_WPM"], "ref": "caydex_voice_achird_v2"}
    key = narration.voice_key(spec)
    assert len(key) <= 80
    assert narration.parse_voice_key(key) == spec


def test_the_kokoro_voice_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(settings, "LEARN_NARRATOR_KOKORO_VOICE", "bm_george")
    assert narration.parse_voice_key(narration.voice_key(narration.voice_spec("kokoro"))) == {"voice": "bm_george"}
    monkeypatch.setattr(settings, "LEARN_NARRATOR_KOKORO_VOICE", "../../etc")
    with pytest.raises(ValueError):
        narration.voice_spec("kokoro")
    with pytest.raises(ValueError):
        narration.voice_spec("gemini")


@pytest.mark.parametrize("bad", ["", None, "a", "a=", "=1", "a=1;;b=2", 5])
def test_an_unreadable_voice_key_is_refused(bad):
    with pytest.raises(ValueError):
        narration.parse_voice_key(bad)


def test_the_audio_path_is_content_addressed_under_the_weekly_prefix():
    sha = "0123456789ab" + "f" * 52
    assert narration.weekly_audio_path(SLUG, sha) == f"audio/weekly/{SLUG}-0123456789ab.m4a"
    for slug, s in [("Bad Slug", sha), ("../x", sha), (SLUG, "F" * 64), (SLUG, "a" * 63), (None, sha)]:
        with pytest.raises(ValueError):
            narration.weekly_audio_path(slug, s)


def test_the_public_url_is_the_seeded_form_and_signs_like_the_catalog(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", SUPABASE_URL + "/")
    url = narration.audio_public_url(_path())
    assert url == f"{SUPABASE_URL}/storage/v1/object/public/money-moves-media/{_path()}"
    assert learn_audio_urls.parse_storage_url(url, allowed={learn_audio_urls.MONEY_MOVES_BUCKET}) == (
        "money-moves-media", _path())
    for bad in ["audio/x.m4a", "../" + _path(), _path() + "?x", None]:
        with pytest.raises(ValueError):
            narration.audio_public_url(bad)


def test_a_junk_engine_setting_falls_back_loudly(monkeypatch, caplog):
    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", "gpu")
    with caplog.at_level("ERROR"):
        assert narration.current_engine(fallback="kokoro") == "kokoro"
        assert narration.current_engine() == "chatterbox"
    assert "LEARN_NARRATOR_ENGINE" in caplog.text


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 2. The timing gate
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_a_valid_table_passes_and_is_normalised():
    blocks = _blocks()
    t, end = _timings(blocks)
    out = narration.validate_timings(blocks, t, duration_seconds=end)
    assert [e["i"] for e in out] == list(range(len(blocks)))
    assert all(isinstance(s["start"], float) for e in out for s in e["sentences"])


def _broken(mutate, blocks=None):
    blocks = blocks or _blocks()
    t, end = _timings(blocks)
    duration = mutate(t)
    return blocks, t, (end if duration is None else duration)


@pytest.mark.parametrize("mutate, reason", [
    (lambda t: t.pop(3) and None, "missing"),
    (lambda t: t.append({**copy.deepcopy(t[-1]), "i": len(t)}) or None, "extra block"),
    (lambda t: t.__setitem__(2, {**t[2], "i": 1}) or None, "appears twice"),
    (lambda t: (t.__setitem__(slice(1, 3), [t[2], t[1]])) or None, "block order"),
    (lambda t: t[0].__setitem__("i", "0") or None, "integer block index"),
    (lambda t: t[0].__setitem__("i", True) or None, "integer block index"),
    (lambda t: t[1].__setitem__("start", float("nan")) or None, "not a finite number"),
    (lambda t: t[1]["sentences"][0].__setitem__("end", float("inf")) or None, "not a finite number"),
    (lambda t: t[0].__setitem__("start", -0.5) or None, "negative"),
    (lambda t: t[0].__setitem__("end", True) or None, "not a number"),
    (lambda t: t[0].__setitem__("end", "1.0") or None, "not a number"),
    (lambda t: t[2].__setitem__("start", t[1]["end"] - 1.0) or None, "not monotonic"),
    (lambda t: t[3].__setitem__("start", t[3]["end"] + 1) or None, "after it ends"),
    (lambda t: 1.0, "past the audio"),
    (lambda t: t[4]["sentences"].pop() and None, "sentence(s)"),
    (lambda t: t[4]["sentences"][0].__setitem__("text", "Record capital spending.") or None, "not the server's"),
    (lambda t: t[4]["sentences"][0].__setitem__("text", t[4]["sentences"][0]["text"] + " ") or None,
     "not the server's"),
    (lambda t: t[4]["sentences"].__setitem__(0, "x") or None, "not an object"),
    (lambda t: t[4].__setitem__("sentences", None) or None, "no sentence list"),
    (lambda t: t.__setitem__(0, 7) or None, "not an object"),
])
def test_a_malformed_table_is_refused_with_a_precise_reason(mutate, reason):
    blocks, t, duration = _broken(mutate)
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        narration.validate_timings(blocks, t, duration_seconds=duration)
    assert reason in str(e.value)


def _para(blocks):
    return next(b for b in blocks if b.dest == ("block", 0, 0))


def test_a_dropped_sentence_is_refused_by_the_seconds_per_word_floor():
    """The defect forced alignment hides: CTC still returns a monotonic, correctly-texted span for a
    sentence that was never voiced — squeezed into a few frames."""
    blocks = _blocks()
    t, end = _timings(blocks)
    entry = t[_para(blocks).i]
    s0, s1 = entry["sentences"][0], entry["sentences"][1]
    s1["start"], s1["end"] = s0["end"], s0["end"] + 0.06          # 6 words in 60 ms
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        narration.validate_timings(blocks, t, duration_seconds=end)
    assert "dropped or truncated" in str(e.value) and "sentence 1" in str(e.value)


def test_a_stretched_sentence_is_refused_by_the_ceiling():
    blocks = _blocks()
    t, end = _timings(blocks)
    callout = next(e for e, b in zip(t, blocks) if b.dest == ("block", 0, 2))
    callout["sentences"][0]["end"] = callout["sentences"][0]["start"] + 30.0
    callout["end"] = callout["sentences"][0]["end"]
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        narration.validate_timings(blocks, t, duration_seconds=10_000)
    assert "stretched" in str(e.value)


def test_a_zero_width_sentence_is_refused():
    blocks = _blocks()
    t, end = _timings(blocks)
    t[0]["sentences"][0]["end"] = t[0]["sentences"][0]["start"]
    with pytest.raises(LearnNarratorRequestInvalid):
        narration.validate_timings(blocks, t, duration_seconds=end)


def test_a_sentence_outside_its_block_window_is_refused():
    blocks = _blocks()
    t, end = _timings(blocks)
    t[1]["end"] = t[1]["sentences"][-1]["end"] - 0.5
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        narration.validate_timings(blocks, t, duration_seconds=end)
    assert "after its block ends" in str(e.value)


def test_rounding_overlap_within_epsilon_is_accepted():
    blocks = _blocks()
    t, end = _timings(blocks, gap=0.0)
    t[2]["start"] = t[1]["end"] - 0.01           # a hair of overlap from atempo + 2-decimal rounding
    t[2]["sentences"][0]["start"] = t[2]["start"]
    narration.validate_timings(blocks, t, duration_seconds=end)


def test_ending_inside_the_duration_slack_is_accepted_and_beyond_it_refused():
    blocks = _blocks()
    t, end = _timings(blocks)
    last = t[-1]["end"]
    narration.validate_timings(blocks, t, duration_seconds=last - 0.49)
    with pytest.raises(LearnNarratorRequestInvalid):
        narration.validate_timings(blocks, t, duration_seconds=last - 0.6)


@pytest.mark.parametrize("bad", [None, {}, "x", 3])
def test_a_table_that_is_not_a_list_is_refused(bad):
    with pytest.raises(LearnNarratorRequestInvalid):
        narration.validate_timings(_blocks(), bad)


def test_an_empty_table_names_every_missing_block():
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        narration.validate_timings(_blocks(), [])
    assert "missing" in str(e.value)


def test_a_negative_or_nan_duration_is_refused():
    blocks = _blocks()
    t, _ = _timings(blocks)
    for d in (float("nan"), -1.0, True):
        with pytest.raises(LearnNarratorRequestInvalid):
            narration.validate_timings(blocks, t, duration_seconds=d)


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 3. The merge — the aligner's shape
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_merge_writes_the_aligners_shape():
    blocks = _blocks()
    t, end = _timings(blocks)
    url = "https://x/storage/v1/object/public/money-moves-media/" + _path()
    original = copy.deepcopy(ARTICLE)
    merged = narration.merge_timings(ARTICLE, blocks, t, duration_seconds=end + 0.3, audio_url=url)
    assert ARTICLE == original, "merge must not mutate its input"
    assert merged["hasAudioVersion"] is True and merged["audioUrl"] == url
    assert merged["audioDurationSeconds"] == int(round(end + 0.3))
    s0, s1 = merged["sections"]
    for blk in (s0["content"][0], s0["content"][1], s0["content"][2], s1["content"][1]):
        assert [x["text"] for x in blk["readAlong"]] == narration.sentence_split(blk["text"])
        assert all(set(x) == {"text", "start", "end"} for x in blk["readAlong"])
        assert all(x["start"] == round(x["start"], 2) for x in blk["readAlong"])
    bullets = s1["content"][0]
    assert len(bullets["itemsReadAlong"]) == 3
    assert bullets["itemsReadAlong"][1] == []                 # the empty item keeps an empty slot
    assert [x["text"] for x in bullets["itemsReadAlong"][2]] == ["Order chips a year ahead.", "Lead times are long."]
    assert "readAlong" not in bullets
    # title / subtitle / section titles are spoken context, not highlighted
    assert "readAlong" not in merged and all("readAlong" not in s for s in merged["sections"])


def test_merge_replaces_stale_read_along_and_keeps_everything_else():
    blocks = _blocks()
    t, end = _timings(blocks)
    stale = copy.deepcopy(ARTICLE)
    stale["sections"][0]["content"][0]["readAlong"] = [{"text": "old", "start": 0, "end": 1}]
    stale["sections"][0]["content"][1]["itemsReadAlong"] = [[{"text": "junk", "start": 0, "end": 1}]]
    merged = narration.merge_timings(stale, blocks, t, duration_seconds=end)
    assert merged["sections"][0]["content"][0]["readAlong"][0]["text"] != "old"
    assert "itemsReadAlong" not in merged["sections"][0]["content"][1]
    assert merged["slug"] == SLUG and merged["sections"][0]["icon"] == "chart.bar.fill"


def test_merge_without_a_duration_uses_the_last_end():
    blocks = _blocks()
    t, end = _timings(blocks)
    merged = narration.merge_timings(ARTICLE, blocks, t)
    assert merged["audioDurationSeconds"] == int(round(t[-1]["end"]))
    assert "audioUrl" in merged and merged["audioUrl"] is None   # untouched when not given


def test_merge_refuses_a_content_that_is_not_the_scripts_article():
    blocks = _blocks()
    t, end = _timings(blocks)
    edited = copy.deepcopy(ARTICLE)
    edited["sections"][1]["content"][1]["text"] = "A different closing line."
    with pytest.raises(ValueError):
        narration.merge_timings(edited, blocks, t, duration_seconds=end)


def test_merge_validates_first():
    blocks = _blocks()
    t, end = _timings(blocks)
    t.pop()
    with pytest.raises(LearnNarratorRequestInvalid):
        narration.merge_timings(ARTICLE, blocks, t, duration_seconds=end)


def test_merged_bundle_timings_round_trip_through_the_gate():
    """A bundled article's own aligned read-along, re-expressed as a worker table, passes the gate
    and merges back to the same readAlong (the shapes are the aligner's, both ways)."""
    article = next(a for a in _articles() if a["slug"] == "costcos-membership-magic")
    blocks = narration.narration_blocks(article)
    table, last = [], 0.0
    for b in blocks:
        kind, idx = b.dest[0], b.dest[1:]
        if kind == "block":
            spans = article["sections"][idx[0]]["content"][idx[1]]["readAlong"]
        elif kind == "item":
            spans = article["sections"][idx[0]]["content"][idx[1]]["itemsReadAlong"][idx[2]]
        else:  # context: the catalog keeps no spans — synthesise them just before the next body
            words = narration.spoken_words(b.text)
            spans = [{"text": s, "start": last, "end": last + 0.3 * narration.spoken_words(s)}
                     for s in narration.sentence_split(b.text)]
            for k in range(1, len(spans)):
                spans[k]["start"] = spans[k - 1]["end"]
                spans[k]["end"] = spans[k]["start"] + 0.3 * narration.spoken_words(spans[k]["text"])
            assert words
        table.append({"i": b.i, "start": spans[0]["start"], "end": spans[-1]["end"], "sentences": spans})
        last = spans[-1]["end"]
    # The catalog's context blocks overlap their body here only because we synthesised them; shift.
    t = 0.0
    for e in table:
        shift = t - e["start"] if e["start"] < t else 0.0
        for s in e["sentences"]:
            s["start"] += shift
            s["end"] += shift
        e["start"], e["end"] = e["sentences"][0]["start"], e["sentences"][-1]["end"]
        t = e["end"]
    merged = narration.merge_timings(article, blocks, table, duration_seconds=t)
    for s in merged["sections"]:
        for blk in s["content"]:
            if "readAlong" in blk:
                assert [x["text"] for x in blk["readAlong"]] == narration.sentence_split(blk["text"])


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 4. Enqueue
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_enqueue_freezes_the_script_engine_voice_and_pipeline(fake):
    piece, job = _media_pending(fake)
    row = fake.row(ledger.JOBS, job["id"])
    assert row["engine"] == "chatterbox" and row["pipeline_version"] == narration.PIPELINE_VERSION
    assert narration.parse_voice_key(row["voice"]) == narration.CHATTERBOX_VOICE
    assert row["script"] == narration.script_json(_blocks())
    assert row["script_sha256"] == narration.script_sha256(_blocks())
    assert all("dest" in e for e in row["script"])


def test_enqueue_for_piece_writes_what_the_sync_setup_writes(fake):
    piece = ledger.create_piece_sync(KEY, c.KIND_DEEP_DIVE, scheduled_publish_at=PUBLISH, hold_until=HOLD,
                                     dry_run=True, sb=fake)
    real = asyncio.run(narration.enqueue_for_piece(piece, copy.deepcopy(ARTICLE), engine="chatterbox", round=1))
    setup = _enqueue_sync(fake, piece, copy.deepcopy(ARTICLE), round=1)   # adopts: same script/engine/voice
    assert setup["id"] == real["id"]


def test_a_retried_enqueue_adopts_and_another_script_on_the_same_round_is_refused(fake):
    piece, job = _media_pending(fake)
    again = asyncio.run(narration.enqueue_for_piece(piece, copy.deepcopy(ARTICLE), engine="chatterbox", round=1))
    assert again["id"] == job["id"]
    edited = copy.deepcopy(ARTICLE)
    edited["title"] = "Another Title"
    assert asyncio.run(narration.enqueue_for_piece(piece, edited, engine="chatterbox", round=1)) is None


@pytest.mark.parametrize("mutate, why", [
    (lambda a: a["sections"][0]["content"][0].__setitem__("text", "word " * 76), "word block cap"),
    (lambda a: a["sections"][0]["content"][0].__setitem__("text", "**"), "no speakable text"),
    (lambda a: a.update({"title": "", "subtitle": "", "sections": []}), "nothing to narrate"),
])
def test_enqueue_refuses_an_unnarratable_draft_before_any_io(fake, mutate, why):
    piece = ledger.create_piece_sync(KEY, c.KIND_DEEP_DIVE, scheduled_publish_at=PUBLISH, hold_until=HOLD,
                                     dry_run=True, sb=fake)
    content = copy.deepcopy(ARTICLE)
    mutate(content)
    with pytest.raises(ValueError) as e:
        asyncio.run(narration.enqueue_for_piece(piece, content, engine="chatterbox", round=1))
    assert why in str(e.value)
    assert fake.tables[ledger.JOBS].rows == []


def test_enqueue_refuses_an_unknown_engine_and_a_piece_without_id(fake):
    with pytest.raises(ValueError):
        asyncio.run(narration.enqueue_for_piece({"id": "x"}, ARTICLE, engine="gpu", round=1))
    with pytest.raises(ValueError):
        asyncio.run(narration.enqueue_for_piece({}, ARTICLE, engine="chatterbox", round=1))


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 5. The worker flows (real ledger on the fake, in-memory Storage)
# ═════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_claim_hands_out_the_wire_view_with_the_frozen_voice(fake):
    piece, job = _media_pending(fake)
    wire, claim = await _claimed(fake)
    assert wire["id"] == job["id"] and wire["engine"] == "chatterbox" and wire["round"] == 1
    assert wire["voice"] == narration.CHATTERBOX_VOICE
    assert wire["pipeline_version"] == narration.PIPELINE_VERSION
    assert wire["blocks"] == [b.wire() for b in _blocks()]
    assert all("dest" not in b for b in wire["blocks"])
    assert re.fullmatch(r"[0-9a-f]{16}", wire["claim_nonce"]) and wire["attempts"] == 1


@pytest.mark.asyncio
async def test_claim_offers_only_the_engines_the_worker_declares(fake):
    _media_pending(fake, engine="chatterbox")
    assert await narration.claim_for_worker(["kokoro"], worker_version="k", now=NOW) is None
    assert await narration.claim_for_worker(["piper", "espeak"], worker_version="k", now=NOW) is None
    assert (await narration.claim_for_worker(["kokoro", "chatterbox"], worker_version="k", now=NOW)) is not None


@pytest.mark.asyncio
async def test_a_job_whose_piece_already_closed_is_cancelled_not_rendered(fake):
    piece, job = _media_pending(fake)
    ledger.transition_sync(piece["id"], from_statuses=(c.MEDIA_PENDING,), to_status=c.EXPIRED, sb=fake)
    assert await narration.claim_for_worker(["chatterbox"], worker_version="t", now=NOW) is None
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_CANCELLED


@pytest.mark.asyncio
async def test_a_job_with_an_unreadable_script_is_failed_and_the_worker_gets_a_fresh_round(fake, alerts):
    """A stored script that no longer hashes would fail every render: it is failed at claim time
    (both worker failures in the one call — re-rendering it is pointless), the piece opens round 2
    from its current draft, and the same claim call hands the worker that round."""
    piece, job = _media_pending(fake)
    fake.row(ledger.JOBS, job["id"])["script"] = [{"i": 0, "text": "x", "role": "body", "dest": ["title"]}]
    wire = await narration.claim_for_worker(["chatterbox"], worker_version="t", now=NOW)
    row = fake.row(ledger.JOBS, job["id"])
    assert row["status"] == c.JOB_FAILED and "script_unreadable" in row["error"]
    assert wire is not None and wire["round"] == 2 and wire["blocks"] == [b.wire() for b in _blocks()]
    assert fake.row(ledger.PIECES, piece["id"])["narration_job_id"] == wire["id"]
    assert alerts.calls == []


@pytest.mark.asyncio
async def test_heartbeat_extends_the_lease_and_refuses_a_stale_claim(fake):
    _media_pending(fake)
    wire, claim = await _claimed(fake)
    out = await narration.heartbeat(wire["id"], claim, now=NOW + timedelta(minutes=5))
    assert out["ok"] is True and ledger._parse_ts(out["lease_until"]) > NOW + timedelta(minutes=10)
    with pytest.raises(LearnNarratorJobNotHeld):
        await narration.heartbeat(wire["id"], WorkerClaim(claim.attempts, "0" * 16), now=NOW)
    with pytest.raises(LearnNarratorJobNotHeld):
        await narration.heartbeat(wire["id"], WorkerClaim(claim.attempts + 1, claim.nonce), now=NOW)
    with pytest.raises(LearnNarratorNotFound):
        await narration.heartbeat("00000000-0000-0000-0000-000000000000", claim, now=NOW)
    with pytest.raises(LearnNarratorNotFound):
        await narration.heartbeat("not-a-uuid", claim, now=NOW)


@pytest.mark.asyncio
async def test_heartbeat_on_a_cancelled_job_says_so(fake):
    piece, _ = _media_pending(fake)
    wire, claim = await _claimed(fake)
    ledger.cancel_open_jobs_sync(piece["id"], reason="regenerate", sb=fake)
    with pytest.raises(LearnNarratorJobNotHeld) as e:
        await narration.heartbeat(wire["id"], claim, now=NOW)
    assert "cancelled" in str(e.value)


@pytest.mark.asyncio
async def test_a_reclaimed_zombie_writes_nothing(fake):
    _media_pending(fake)
    wire, old = await _claimed(fake)
    later = NOW + timedelta(seconds=c.JOB_LEASE_SECONDS + 5)              # the lease ran out
    wire2 = await narration.claim_for_worker(["chatterbox"], worker_version="t", now=later)
    assert wire2["id"] == wire["id"] and wire2["attempts"] == 2
    with pytest.raises(LearnNarratorJobNotHeld):
        await narration.heartbeat(wire["id"], old, now=later)
    with pytest.raises(LearnNarratorJobNotHeld):
        await narration.upload_target(wire["id"], old, content_type="audio/mp4", size_bytes=10, sha256="a" * 64)


# ── upload ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_mints_a_signed_url_for_the_content_addressed_path(fake, caplog):
    _media_pending(fake)
    wire, claim = await _claimed(fake)
    sha = "cd" * 32
    with caplog.at_level("INFO"):
        out = await narration.upload_target(wire["id"], claim, content_type="audio/mp4", size_bytes=1000, sha256=sha)
    assert out["path"] == f"audio/weekly/{SLUG}-{sha[:12]}.m4a" and out["already_uploaded"] is False
    assert out["upload_url"].startswith("https://fake.storage/upload/sign/money-moves-media/")
    assert ("sign", "money-moves-media", out["path"]) in fake.storage.calls
    assert "SECRET-TOKEN" not in caplog.text, "the signed URL is a write credential: never logged"


@pytest.mark.asyncio
async def test_a_reupload_of_the_same_object_gets_no_url(fake):
    _media_pending(fake)
    wire, claim = await _claimed(fake)
    sha = "cd" * 32
    fake.storage.put(f"audio/weekly/{SLUG}-{sha[:12]}.m4a", 1000)
    out = await narration.upload_target(wire["id"], claim, content_type="audio/mp4", size_bytes=1000, sha256=sha)
    assert out["upload_url"] is None and out["already_uploaded"] is True
    assert not any(call[0] == "sign" for call in fake.storage.calls)


@pytest.mark.asyncio
async def test_another_object_at_the_path_is_refused_and_left_in_place(fake):
    _media_pending(fake)
    wire, claim = await _claimed(fake)
    sha = "cd" * 32
    path = f"audio/weekly/{SLUG}-{sha[:12]}.m4a"
    fake.storage.put(path, 999)
    with pytest.raises(LearnNarratorAssetMismatch):
        await narration.upload_target(wire["id"], claim, content_type="audio/mp4", size_bytes=1000, sha256=sha)
    assert (narration.AUDIO_BUCKET, path) in fake.storage.objects
    fake.storage.put(path, None)
    with pytest.raises(LearnNarratorLedgerError):          # unknown size: retried, nothing decided
        await narration.upload_target(wire["id"], claim, content_type="audio/mp4", size_bytes=1000, sha256=sha)


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs, why", [
    ({"content_type": "audio/mpeg"}, "content_type"),
    ({"content_type": "AUDIO/MP4"}, "content_type"),
    ({"size_bytes": 0}, "bytes"),
    ({"size_bytes": narration.MAX_AUDIO_UPLOAD_BYTES + 1}, "bytes"),
    ({"size_bytes": True}, "bytes"),
    ({"size_bytes": 10.5}, "bytes"),
    ({"sha256": "AB" * 32}, "sha256"),
    ({"sha256": "ab" * 31}, "sha256"),
])
async def test_upload_refuses_a_bad_request_before_any_io(fake, kwargs, why):
    _media_pending(fake)
    wire, claim = await _claimed(fake)
    args = {"content_type": "audio/mp4", "size_bytes": 1000, "sha256": "cd" * 32, **kwargs}
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        await narration.upload_target(wire["id"], claim, **args)
    assert why in str(e.value)
    assert fake.storage.calls == []


@pytest.mark.asyncio
async def test_a_storage_outage_at_upload_is_retryable(fake):
    _media_pending(fake)
    wire, claim = await _claimed(fake)
    fake.storage.failures["list"] = RuntimeError("storage down")
    with pytest.raises(LearnNarratorLedgerError):
        await narration.upload_target(wire["id"], claim, content_type="audio/mp4", size_bytes=1000, sha256="cd" * 32)


# ── complete ────────────────────────────────────────────────────────────────────────────────


async def _rendered(fake, *, spw=0.36, size=None, mimetype="audio/mp4", sha="ef" * 32):
    """Claim, 'render' and 'upload': (wire, claim, kwargs for complete)."""
    wire, claim = await _claimed(fake)
    blocks = _blocks()
    t, end = _timings(blocks, spw=spw)
    path = f"audio/weekly/{SLUG}-{sha[:12]}.m4a"
    size = size if size is not None else int(end * 12_000)
    fake.storage.put(path, size, mimetype)
    kwargs = dict(path=path, size_bytes=size, duration_seconds=end, engine="chatterbox",
                  pipeline_version=narration.PIPELINE_VERSION, timings=t,
                  checks={"blocks": [{"i": 0, "wps": 2.7}], "coverage": 0.99})
    return wire, claim, kwargs


@pytest.mark.asyncio
async def test_complete_merges_the_audio_and_hands_the_piece_to_review(fake):
    piece, job = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    out = await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert out == {"ok": True, "status": c.JOB_READY, "replay": False, "piece_updated": True}
    jrow = fake.row(ledger.JOBS, job["id"])
    assert jrow["status"] == c.JOB_READY and jrow["audio_path"] == kw["path"]
    assert jrow["checks"]["worker"]["coverage"] == 0.99
    assert jrow["checks"]["server"]["sentences"] > 5 and 11_500 <= jrow["checks"]["server"]["bytes_per_second"] <= 14_000
    prow = fake.row(ledger.PIECES, piece["id"])
    assert prow["status"] == c.READY_FOR_REVIEW
    draft = prow["draft"]
    assert prow["draft_sha256"] == narration.draft_sha256(draft) != piece["draft_sha256"]
    assert draft["hasAudioVersion"] is True
    assert draft["audioUrl"] == f"{SUPABASE_URL}/storage/v1/object/public/money-moves-media/{kw['path']}"
    assert draft["sections"][0]["content"][0]["readAlong"]
    assert draft["audioDurationSeconds"] == int(round(kw["duration_seconds"]))


@pytest.mark.asyncio
async def test_a_replayed_complete_answers_200_and_writes_nothing(fake):
    piece, _ = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    await narration.complete(wire["id"], claim, now=NOW, **kw)
    before = copy.deepcopy({k: v.rows for k, v in fake.tables.items()})
    writes = sum(1 for t, op in fake.calls if op in ("update", "insert", "upsert"))
    out = await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert out["replay"] is True and out["piece_updated"] is False and out["status"] == c.JOB_READY
    assert sum(1 for t, op in fake.calls if op in ("update", "insert", "upsert")) == writes
    assert before == {k: v.rows for k, v in fake.tables.items()}


@pytest.mark.asyncio
async def test_a_replay_after_a_lost_piece_write_finishes_the_piece(fake):
    """complete_job landed, then the piece write failed (a Supabase blip): the worker's retry is a
    replay — and it is the one that must not leave the piece stuck in media_pending."""
    piece, _ = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    fake.fail_next(ledger.PIECES, "update", RuntimeError("connection reset"))
    with pytest.raises(LearnNarratorLedgerError):
        await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert fake.row(ledger.PIECES, piece["id"])["status"] == c.MEDIA_PENDING
    out = await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert out["replay"] is True and out["piece_updated"] is True
    assert fake.row(ledger.PIECES, piece["id"])["status"] == c.READY_FOR_REVIEW


@pytest.mark.asyncio
async def test_complete_on_a_cancelled_job_answers_200_and_writes_nothing(fake):
    piece, job = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    ledger.cancel_open_jobs_sync(piece["id"], reason="owner regenerate", sb=fake)
    snapshot = copy.deepcopy({k: v.rows for k, v in fake.tables.items()})
    out = await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert out == {"ok": True, "status": c.JOB_CANCELLED, "replay": False, "piece_updated": False}
    assert {k: v.rows for k, v in fake.tables.items()} == snapshot
    assert (narration.AUDIO_BUCKET, kw["path"]) in fake.storage.objects   # nothing deleted either


@pytest.mark.asyncio
async def test_complete_with_a_stale_claim_is_refused(fake):
    _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    with pytest.raises(LearnNarratorJobNotHeld):
        await narration.complete(wire["id"], WorkerClaim(claim.attempts, "f" * 16), now=NOW, **kw)
    with pytest.raises(LearnNarratorNotFound):
        await narration.complete("00000000-0000-0000-0000-000000000000", claim, now=NOW, **kw)


@pytest.mark.asyncio
async def test_a_missing_object_is_409_and_leaves_the_job_held(fake):
    piece, job = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    fake.storage.objects.clear()
    with pytest.raises(LearnNarratorAssetMissing):
        await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_IN_PROGRESS


@pytest.mark.asyncio
@pytest.mark.parametrize("object_size, mimetype, why", [
    (None, "audio/mp4", "size"),                      # the registered size differs (set below)
    ("same", "video/mp4", "content type"),
])
async def test_an_object_that_is_not_the_registered_one_is_deleted_and_the_round_failed(
        fake, object_size, mimetype, why):
    piece, job = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    fake.storage.put(kw["path"], kw["size_bytes"] - 1 if object_size is None else kw["size_bytes"], mimetype)
    with pytest.raises(LearnNarratorAssetMismatch) as e:
        await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert why in str(e.value)
    assert (narration.AUDIO_BUCKET, kw["path"]) not in fake.storage.objects
    jrow = fake.row(ledger.JOBS, job["id"])
    assert jrow["status"] == c.JOB_QUEUED and jrow["worker_failures"] == 1 and "asset_mismatch" in jrow["error"]
    assert fake.row(ledger.PIECES, piece["id"])["status"] == c.MEDIA_PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize("bytes_per_second", [5_000, 9_700, 11_000, 14_100, 16_000, 40_000])
async def test_audio_outside_the_engines_byte_rate_is_refused(fake, bytes_per_second):
    """9,700 = the Gemini path's default encode, 16,000 = a 128k encode. An asset mismatch stays on the
    retry path (not `contracts.DETERMINISTIC_FAIL_CODES`): the job is re-queued under its cap."""
    _, job = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    size = int(kw["duration_seconds"] * bytes_per_second)
    fake.storage.put(kw["path"], size)
    kw["size_bytes"] = size
    with pytest.raises(LearnNarratorAssetMismatch) as e:
        await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert "bytes/s" in str(e.value)
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_QUEUED


@pytest.mark.asyncio
@pytest.mark.parametrize("bytes_per_second", [12_629, 12_888, 13_990])
async def test_the_narrator_images_encode_rate_is_accepted(fake, bytes_per_second):
    """Railway's ffmpeg 5.1 encodes `-b:a 96k` above the local catalog's ~12.1k: the 2026-10-24 piece at
    12,629 B/s, the 2026-10-30 piece at 12,888 — refused twice under the old 12,800 ceiling."""
    piece, job = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    size = int(kw["duration_seconds"] * bytes_per_second)
    fake.storage.put(kw["path"], size)
    kw["size_bytes"] = size
    out = await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert out["status"] == c.JOB_READY
    assert round(fake.row(ledger.JOBS, job["id"])["checks"]["server"]["bytes_per_second"]) == bytes_per_second
    assert fake.row(ledger.PIECES, piece["id"])["status"] == c.READY_FOR_REVIEW


def test_the_kokoro_band_is_looser_and_still_catches_silence():
    assert narration.bytes_per_second_problem("kokoro", 10_500 * 100, 100.0) is None
    assert narration.bytes_per_second_problem("chatterbox", 10_500 * 100, 100.0)
    assert narration.bytes_per_second_problem("kokoro", 4_000 * 100, 100.0)
    assert narration.bytes_per_second_problem("unknown", 12_000 * 100, 100.0) is None   # strictest band
    assert narration.bytes_per_second_problem("unknown", 10_500 * 100, 100.0)
    for engine in ("chatterbox", "kokoro"):                 # the ceiling: a 128k encode is never the voice
        assert narration.bytes_per_second_problem(engine, 14_000 * 100, 100.0) is None
        assert narration.bytes_per_second_problem(engine, 14_001 * 100, 100.0)


@pytest.mark.asyncio
async def test_an_unknown_object_size_deletes_nothing_and_is_retried(fake):
    piece, job = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    fake.storage.put(kw["path"], None)
    with pytest.raises(LearnNarratorLedgerError):
        await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert (narration.AUDIO_BUCKET, kw["path"]) in fake.storage.objects
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_IN_PROGRESS


@pytest.mark.asyncio
@pytest.mark.parametrize("change, why", [
    ({"engine": "kokoro"}, "frozen"),
    ({"pipeline_version": "learn-narration/v0"}, "pipeline_version"),
    ({"path": f"audio/weekly/another-slug-{'ef' * 6}.m4a"}, "slug"),
    ({"path": f"audio/{SLUG}.m4a"}, "weekly narration path"),
    ({"path": None}, "weekly narration path"),
    ({"size_bytes": -5}, "bytes"),
    ({"duration_seconds": float("nan")}, "duration_seconds"),
    ({"duration_seconds": 0}, "duration_seconds"),
    ({"duration_seconds": 5 * 3600.0}, "duration_seconds"),
    ({"checks": {"x": float("inf")}}, "checks"),
    ({"checks": ["x"]}, "checks"),
])
async def test_complete_refuses_a_contract_breach_before_touching_storage(fake, change, why):
    piece, job = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    fake.storage.calls.clear()
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        await narration.complete(wire["id"], claim, now=NOW, **{**kw, **change})
    assert why in str(e.value)
    assert fake.storage.calls == []
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_IN_PROGRESS


@pytest.mark.asyncio
async def test_a_bad_timing_table_is_refused_before_storage(fake):
    _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    fake.storage.calls.clear()
    kw["timings"] = kw["timings"][:-1]
    with pytest.raises(LearnNarratorRequestInvalid):
        await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert fake.storage.calls == []


@pytest.mark.asyncio
async def test_a_piece_that_moved_on_keeps_its_state_and_the_job_still_completes(fake):
    piece, job = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    # Regenerate opened round 2 meanwhile (but did not cancel this job): the piece waits on another job.
    other = await narration.enqueue_for_piece(piece, copy.deepcopy(ARTICLE), engine="chatterbox", round=2)
    ledger.transition_sync(piece["id"], from_statuses=(c.MEDIA_PENDING,), to_status=c.MEDIA_PENDING,
                           fields={"narration_job_id": other["id"]}, sb=fake)
    out = await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert out["status"] == c.JOB_READY and out["piece_updated"] is False
    prow = fake.row(ledger.PIECES, piece["id"])
    assert prow["status"] == c.MEDIA_PENDING and prow["draft"] == ARTICLE


@pytest.mark.asyncio
async def test_a_piece_that_moves_on_between_the_read_and_the_write_is_not_written(fake):
    """The compare-and-swap itself, not only the read-time check: another writer points the piece at
    a new round after `_finish_piece` read it — the fenced UPDATE must match nothing."""
    piece, job = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    newer = "11111111-2222-3333-4444-555555555555"
    fake.before_update[ledger.PIECES] = [lambda: fake.row(ledger.PIECES, piece["id"]).update(
        {"narration_job_id": newer})]
    out = await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert out["status"] == c.JOB_READY and out["piece_updated"] is False
    prow = fake.row(ledger.PIECES, piece["id"])
    assert prow["status"] == c.MEDIA_PENDING and prow["narration_job_id"] == newer and prow["draft"] == ARTICLE


@pytest.mark.asyncio
async def test_a_draft_changed_under_the_job_is_not_merged(fake, caplog):
    piece, job = _media_pending(fake)
    wire, claim, kw = await _rendered(fake)
    edited = copy.deepcopy(ARTICLE)
    edited["subtitle"] = "A rewritten subtitle."
    ledger.transition_sync(piece["id"], from_statuses=(c.MEDIA_PENDING,), to_status=c.MEDIA_PENDING,
                           fields={"draft": edited, "draft_sha256": narration.draft_sha256(edited)}, sb=fake)
    with caplog.at_level("ERROR"):
        out = await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert out["piece_updated"] is False and "no longer the article" in caplog.text
    assert fake.row(ledger.PIECES, piece["id"])["draft"] == edited


# ── fail ────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_first_failure_requeues_the_job(fake, alerts):
    piece, job = _media_pending(fake)
    wire, claim = await _claimed(fake)
    out = await narration.fail(wire["id"], claim, code="block_gate", detail="block 3 at 1.2 w/s", now=NOW)
    assert out == {"ok": True, "status": c.JOB_QUEUED, "worker_failures": 1}
    assert fake.row(ledger.PIECES, piece["id"])["status"] == c.MEDIA_PENDING
    assert alerts.calls == []


async def _fail_twice(fake, now):
    for _ in range(c.MAX_JOB_WORKER_FAILURES):
        wire, claim = await _claimed(fake, now=now)
        out = await narration.fail(wire["id"], claim, code="render_failed", detail="OOM", now=now)
    return wire, claim, out


@pytest.mark.asyncio
async def test_a_failed_job_inside_the_cutoff_opens_the_next_round(fake, alerts):
    piece, job = _media_pending(fake)
    wire, claim, out = await _fail_twice(fake, NOW)
    assert out["status"] == c.JOB_FAILED
    jobs = ledger.list_jobs_sync(piece["id"], sb=fake)
    assert [(j["round"], j["status"]) for j in jobs] == [(1, c.JOB_FAILED), (2, c.JOB_QUEUED)]
    prow = fake.row(ledger.PIECES, piece["id"])
    assert prow["status"] == c.MEDIA_PENDING and prow["narration_job_id"] == jobs[1]["id"]
    assert "narration round 1 failed" in prow["last_error"]
    assert alerts.calls == []


@pytest.mark.asyncio
async def test_a_failed_job_past_the_cutoff_fails_the_piece_and_alerts(fake, alerts):
    piece, job = _media_pending(fake)
    words = sum(len(b.text.split()) for b in _blocks())
    late = schedule.catch_up_cutoff(HOLD, "chatterbox", words, build_seconds=0) + timedelta(minutes=1)
    wire, claim, out = await _fail_twice(fake, late)
    prow = fake.row(ledger.PIECES, piece["id"])
    assert prow["status"] == c.FAILED and prow["last_stage"] == "narration"
    assert "narration_cutoff_passed" in prow["last_error"]
    assert alerts.calls == [{"piece_id": piece["id"], "status": c.FAILED, "reason": "narration_cutoff_passed",
                             "stage": "narration"}]
    # a replay of the recorded failure moves nothing and alerts nothing more
    again = await narration.fail(wire["id"], claim, code="render_failed", detail="OOM", now=late)
    assert again["status"] == c.JOB_FAILED and len(alerts.calls) == 1


@pytest.mark.asyncio
async def test_rounds_are_bounded_in_count_too(fake, alerts):
    piece, _ = _media_pending(fake)
    for _ in range(narration.MAX_AUTO_FAILED_ROUNDS):
        await _fail_twice(fake, NOW)
    prow = fake.row(ledger.PIECES, piece["id"])
    assert prow["status"] == c.FAILED and "narration_rounds_exhausted" in prow["last_error"]
    assert [a["reason"] for a in alerts.calls] == ["narration_rounds_exhausted"]
    assert len(ledger.list_jobs_sync(piece["id"], sb=fake)) == narration.MAX_AUTO_FAILED_ROUNDS


@pytest.mark.asyncio
async def test_the_next_round_uses_the_current_engine_setting(fake, alerts, monkeypatch):
    piece, _ = _media_pending(fake)
    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", "kokoro")
    monkeypatch.setattr(settings, "LEARN_NARRATOR_KOKORO_VOICE", "am_michael")
    await _fail_twice(fake, NOW)
    jobs = ledger.list_jobs_sync(piece["id"], sb=fake)
    assert jobs[1]["engine"] == "kokoro" and narration.parse_voice_key(jobs[1]["voice"]) == {"voice": "am_michael"}


@pytest.mark.asyncio
async def test_an_asset_mismatch_at_the_cap_moves_the_piece_like_a_failure(fake, alerts):
    piece, job = _media_pending(fake)
    wire, claim = await _claimed(fake)
    await narration.fail(wire["id"], claim, code="render_failed", detail="x", now=NOW)
    wire, claim, kw = await _rendered(fake)
    fake.storage.put(kw["path"], 7)
    with pytest.raises(LearnNarratorAssetMismatch):
        await narration.complete(wire["id"], claim, now=NOW, **kw)
    jobs = ledger.list_jobs_sync(piece["id"], sb=fake)
    assert [(j["round"], j["status"]) for j in jobs] == [(1, c.JOB_FAILED), (2, c.JOB_QUEUED)]


@pytest.mark.asyncio
async def test_fail_on_a_cancelled_job_answers_cancelled(fake):
    piece, _ = _media_pending(fake)
    wire, claim = await _claimed(fake)
    ledger.cancel_open_jobs_sync(piece["id"], sb=fake)
    out = await narration.fail(wire["id"], claim, code="x", detail="y", now=NOW)
    assert out["status"] == c.JOB_CANCELLED


@pytest.mark.asyncio
@pytest.mark.parametrize("code, detail", [("", ""), ("Bad Code", ""), ("x" * 65, ""), (None, ""), ("ok", 5)])
async def test_fail_refuses_a_malformed_code(fake, code, detail):
    _media_pending(fake)
    wire, claim = await _claimed(fake)
    with pytest.raises(LearnNarratorRequestInvalid):
        await narration.fail(wire["id"], claim, code=code, detail=detail, now=NOW)


@pytest.mark.asyncio
async def test_after_job_failed_is_idempotent_and_ignores_a_piece_on_another_job(fake, alerts):
    piece, job = _media_pending(fake)
    await _fail_twice(fake, NOW)
    failed = fake.row(ledger.JOBS, job["id"])
    before = copy.deepcopy(fake.row(ledger.PIECES, piece["id"]))
    await narration.after_job_failed(failed, now=NOW)          # e.g. the scheduler's sweep, again
    assert fake.row(ledger.PIECES, piece["id"]) == before
    assert len(ledger.list_jobs_sync(piece["id"], sb=fake)) == 2


# ── deterministic failures: no retry that renders the same file ─────────────────────────────
#
# 2026-10-30: the W44 Deep Dive failed `voice_out_of_band` at 12,888 B/s, then at 12,887 on round 2 —
# the render is seeded per block and encoded bitexact, so every retry of the same job is the same
# file, ~1.5 h of 4 vCPU each.

#: Every code the worker reports that is NOT judged on the finished file: kills, timeouts, the
#: upload, the API, a bug, a block gate, a refused call. Each keeps the retry path.
_RETRIED_CODES = ("render_oom", "render_part_oom", "render_timeout", "render_failed", "render_part_failed",
                  "align_oom", "align_timeout", "align_failed", "encode_failed", "upload_failed", "api_error",
                  "worker_error", "request_refused", "block_gate", "weights_missing", "job_invalid")


def test_the_deterministic_codes_are_the_two_judged_on_the_finished_file():
    assert c.DETERMINISTIC_FAIL_CODES == frozenset({"voice_out_of_band", "timings_invalid"})
    assert not c.DETERMINISTIC_FAIL_CODES & set(_RETRIED_CODES)


@pytest.mark.parametrize("error, code", [
    ("voice_out_of_band: bytes_per_sec 12888.0 outside (11500, 12800)", "voice_out_of_band"),
    ("  timings_invalid: 2 problem(s)", "timings_invalid"),
    ("asset_mismatch: size 7 != 9", "asset_mismatch"),
    ("lease_reclaims_exhausted: the lease ran out 5 times", "lease_reclaims_exhausted"),
    ("voice_out_of_band", None),            # no detail separator: not the stored "<code>: <detail>" form
    ("Voice_Out_Of_Band: x", None),
    (": x", None),
    ("", None),
    (None, None),
    (42, None),
])
def test_fail_code_of_reads_the_stored_prefix_only(error, code):
    assert c.fail_code_of(error) == code


@pytest.mark.asyncio
@pytest.mark.parametrize("code", sorted(c.DETERMINISTIC_FAIL_CODES))
async def test_a_deterministic_code_fails_the_job_and_the_piece_at_its_first_report(fake, alerts, code):
    piece, job = _media_pending(fake)
    wire, claim = await _claimed(fake)
    out = await narration.fail(wire["id"], claim, code=code, detail="bytes_per_sec 12888.0 outside", now=NOW)
    assert out == {"ok": True, "status": c.JOB_FAILED, "worker_failures": 1}
    jrow = fake.row(ledger.JOBS, job["id"])
    assert jrow["status"] == c.JOB_FAILED and jrow["finished_at"] and jrow["lease_until"] is None
    assert jrow["error"].startswith(f"{code}: ")
    # no automatic round: the same engine, script, voice and pipeline would render the same file
    assert [j["round"] for j in ledger.list_jobs_sync(piece["id"], sb=fake)] == [1]
    prow = fake.row(ledger.PIECES, piece["id"])
    assert prow["status"] == c.FAILED and prow["last_stage"] == "narration"
    assert prow["last_error"].startswith(f"narration_deterministic_failure: round 1: {code}: ")
    assert alerts.calls == [{"piece_id": piece["id"], "status": c.FAILED, "stage": "narration",
                             "reason": f"narration_deterministic_failure: {code} — a new round on chatterbox "
                                       f"would render the same file; fix the cause, then rebuild"}]
    # a replay of the recorded failure moves nothing and alerts nothing more; neither does the sweep
    again = await narration.fail(wire["id"], claim, code=code, detail="again", now=NOW)
    assert again["status"] == c.JOB_FAILED
    await narration.after_job_failed(fake.row(ledger.JOBS, job["id"]), now=NOW)
    assert len(alerts.calls) == 1 and fake.row(ledger.JOBS, job["id"])["worker_failures"] == 1
    assert await narration.claim_for_worker(["chatterbox"], worker_version="t", now=NOW) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("code", _RETRIED_CODES)
async def test_every_other_code_still_requeues_the_job(fake, alerts, code):
    piece, job = _media_pending(fake)
    wire, claim = await _claimed(fake)
    out = await narration.fail(wire["id"], claim, code=code, detail="x", now=NOW)
    assert out == {"ok": True, "status": c.JOB_QUEUED, "worker_failures": 1}
    assert fake.row(ledger.PIECES, piece["id"])["status"] == c.MEDIA_PENDING and alerts.calls == []
    again, _ = await _claimed(fake)                    # the same job, rendered once more
    assert again["id"] == job["id"] and again["round"] == 1


@pytest.mark.asyncio
async def test_a_deterministic_code_on_the_second_attempt_opens_no_round(fake, alerts):
    """A kill first (re-queued), then a finished file out of band: the job is failed at the cap either
    way, but the piece gets no round that would render that file again."""
    piece, job = _media_pending(fake)
    wire, claim = await _claimed(fake)
    await narration.fail(wire["id"], claim, code="render_part_oom", detail="exit -9", now=NOW)
    wire, claim = await _claimed(fake)
    out = await narration.fail(wire["id"], claim, code="voice_out_of_band", detail="x", now=NOW)
    assert out["status"] == c.JOB_FAILED and out["worker_failures"] == 2
    assert len(ledger.list_jobs_sync(piece["id"], sb=fake)) == 1
    assert fake.row(ledger.PIECES, piece["id"])["status"] == c.FAILED
    assert [a["reason"].split(":")[0] for a in alerts.calls] == ["narration_deterministic_failure"]


@pytest.mark.asyncio
async def test_a_deterministic_failure_still_gets_a_round_on_another_engine(fake, alerts, monkeypatch):
    piece, job = _media_pending(fake)
    wire, claim = await _claimed(fake)
    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", "kokoro")
    monkeypatch.setattr(settings, "LEARN_NARRATOR_KOKORO_VOICE", "am_michael")
    out = await narration.fail(wire["id"], claim, code="voice_out_of_band", detail="x", now=NOW)
    assert out["status"] == c.JOB_FAILED
    jobs = ledger.list_jobs_sync(piece["id"], sb=fake)
    assert [(j["round"], j["status"], j["engine"]) for j in jobs] == [(1, c.JOB_FAILED, "chatterbox"),
                                                                       (2, c.JOB_QUEUED, "kokoro")]
    prow = fake.row(ledger.PIECES, piece["id"])
    assert prow["status"] == c.MEDIA_PENDING and prow["narration_job_id"] == jobs[1]["id"]
    assert alerts.calls == []


@pytest.mark.asyncio
async def test_a_deterministic_failure_still_gets_a_round_when_the_render_inputs_changed(fake, alerts, monkeypatch):
    """A new pipeline version asks the worker for something else, so the file would not be the same."""
    piece, job = _media_pending(fake)
    wire, claim = await _claimed(fake)
    monkeypatch.setattr(narration, "PIPELINE_VERSION", "learn-narration/v2")
    await narration.fail(wire["id"], claim, code="timings_invalid", detail="x", now=NOW)
    jobs = ledger.list_jobs_sync(piece["id"], sb=fake)
    assert [(j["round"], j["status"], j["pipeline_version"]) for j in jobs] == [
        (1, c.JOB_FAILED, "learn-narration/v1"), (2, c.JOB_QUEUED, "learn-narration/v2")]
    assert alerts.calls == []


def test_repeats_render_compares_engine_script_voice_and_pipeline(fake):
    piece, job = _media_pending(fake)
    assert narration.repeats_render(job, ARTICLE, "chatterbox") is True
    assert narration.repeats_render(job, ARTICLE, "kokoro") is False
    # the engine on its own (the two engines' voice keys never coincide, so this isolates it)
    assert narration.repeats_render({**job, "engine": "kokoro"}, ARTICLE, "chatterbox") is False
    assert narration.repeats_render({**job, "voice": "exaggeration=0.5"}, ARTICLE, "chatterbox") is False
    assert narration.repeats_render({**job, "pipeline_version": "learn-narration/v0"}, ARTICLE, "chatterbox") is False
    changed = copy.deepcopy(ARTICLE)
    changed["title"] = changed["title"] + " Revisited"
    assert narration.repeats_render(job, changed, "chatterbox") is False
    assert narration.repeats_render(job, None, "chatterbox") is False      # the enqueue refuses it, loudly


@pytest.mark.asyncio
async def test_a_cancelled_job_is_never_read_as_a_deterministic_failure(fake, alerts):
    """The sweep hands `after_job_failed` a CANCELLED job too (a Regenerate racing a Retry narration);
    only a job that FAILED with a deterministic code stops the rounds."""
    piece, job = _media_pending(fake)
    ledger.cancel_open_jobs_sync(piece["id"], sb=fake)
    cancelled = {**fake.row(ledger.JOBS, job["id"]), "error": "voice_out_of_band: an earlier attempt"}
    await narration.after_job_failed(cancelled, now=NOW)
    assert [j["round"] for j in ledger.list_jobs_sync(piece["id"], sb=fake)] == [1, 2]
    assert fake.row(ledger.PIECES, piece["id"])["status"] == c.MEDIA_PENDING and alerts.calls == []


# ── the alert hook ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_alert_hook_never_raises(monkeypatch, caplog):
    mod = types.ModuleType("app.services.learn_weekly.alerts")

    async def boom(*a, **k):
        raise RuntimeError("telegram down")

    mod.notify_terminal = boom
    monkeypatch.setitem(sys.modules, "app.services.learn_weekly.alerts", mod)
    with caplog.at_level("ERROR"):
        await narration._notify_terminal({"id": "p1", "period_key": KEY}, status=c.FAILED, reason="r", stage="narration")
    assert "could not be sent" in caplog.text and "telegram down" in caplog.text
    monkeypatch.setitem(sys.modules, "app.services.learn_weekly.alerts", None)   # import → ImportError
    await narration._notify_terminal({"id": "p1"}, status=c.FAILED, reason="r", stage=None)


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 6. Source guards
# ═════════════════════════════════════════════════════════════════════════════════════════════


def _code(path: Path) -> str:
    """Source with comments and docstrings removed (testing.md §3: a guard must not pass on prose)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and node.body \
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) \
                and isinstance(node.body[0].value.value, str):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


_NARRATION = _BACKEND / "app" / "services" / "learn_weekly" / "narration.py"


def test_narration_never_imports_the_scripts_tree_fmp_or_torch():
    tree = ast.parse(_NARRATION.read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert not any(m.startswith(("scripts", "torch", "torchaudio", "chatterbox", "app.integrations")) for m in mods), mods
    code = _code(_NARRATION)
    assert "fmp" not in code.lower() and "gemini" not in code.lower()


def test_storage_calls_only_run_off_the_event_loop():
    """Every Storage verb sits in a plain `def`, and every async caller hands it to to_thread."""
    tree = ast.parse(_NARRATION.read_text(encoding="utf-8"))
    sync_storage = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            src = ast.unparse(node)
            if ".storage." in src:
                sync_storage.add(node.name)
        if isinstance(node, ast.AsyncFunctionDef):
            assert ".storage." not in ast.unparse(node), node.name
    assert sync_storage == {"_object_stat_sync", "_remove_object_sync", "_signed_upload_url_sync"}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in sync_storage:
                    pytest.fail(f"{node.name} calls {call.func.id} directly — run it through asyncio.to_thread")
