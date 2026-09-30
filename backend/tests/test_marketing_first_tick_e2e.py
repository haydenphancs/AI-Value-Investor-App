"""
End to end: the REAL worker (`marketing/main.py` `main()`) against the REAL web app — the internal
router, its token and claim gates, the real `MarketingRunService` and the real script service —
over the in-memory PostgREST + Storage fake of test_marketing_run_service.py. Hermetic.

Why this exists (review 2026-09-29): the worker's whole conversation used to be tested only
against a hand-written FakeBackend (tests/test_marketing_worker.py). Drift between that fake and
the real routes, schemas or ledger fences would have shipped unseen, and the first Railway cron
tick would have been the first integration test. Here every call crosses the real FastAPI app.

What is faked, and only this:
* the model: the day's ACCEPTED package is seeded into `marketing_scripts` when the first kick
  arrives — exactly what a finished writer leaves (a background generation task would not survive
  a lifespan-less TestClient between requests);
* the Kokoro child and the AAC encode (a narration table for exactly the narrated lines — the
  server's `_check_timed_words` still compares it with the script);
* the render's ffmpeg call and the public-URL download (`render.produce_video`, `render.download`)
  — the card texts still come from the REAL `cards.py`, and the server's on-screen-text check
  judges them;
* Supabase Storage: signed-upload PUTs land in the fake bucket with the size and content type of
  the multipart part the worker actually sent, so the server's object verification is real.
"""

from __future__ import annotations

import email
import functools
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import marketing_internal as mod
from app.config import settings
from app.main import app
from app.services.marketing import run_service as mrs
from app.services.marketing import script_service as ss
from test_marketing_run_service import FakeSupabase

_PKG = Path(__file__).resolve().parents[1] / "marketing"
_TOKEN = "e2e-worker-token"
_BACKEND = "backend.test"

_PACKAGE = {
    "hook": "Meet your moody business partner.",
    "video_script": ["Every day he names a price for your share.",
                     "His price follows his mood, not the business."],
    "cards": [{"title": "The partner", "body": "He names a price every day."},
              {"title": "The lesson", "body": "His mood is not the business."}],
    "carousel_slides": [],
    "disclaimer_card": ("Educational, impersonal information — not investment advice. Investing "
                        "involves risk. Script and narration generated with AI. Caydex · Sep 17, 2026"),
    "posts": {
        "tiktok": {"platform": "tiktok", "title": None, "caption": "server TikTok copy"},
        "x": {"platform": "x", "title": None, "caption": "server X copy"},
    },
    "judge": {"mode": "enforce", "verdicts": []},
    "template_id": "case_story",
}


def _load_worker():
    spec = importlib.util.spec_from_file_location("marketing_worker_e2e_under_test", _PKG / "main.py")
    worker = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = worker
    spec.loader.exec_module(worker)  # type: ignore[union-attr]
    return worker


def _narration(lines, *, voice, speed, out_dir, heartbeat=None, timeout=None):
    from marketing import timings as tm
    from marketing import voice as vc

    per_line = [(tm.proportional(line.split(), 0.0, 1.0), 1.0) for line in lines]
    words = tm.as_table(tm.assemble(lines, per_line))
    return vc.Narration(Path(out_dir) / "narration.wav", words, 1.0 * len(lines), speed, len(lines))


class World:
    """The worker + the real app + the fake database/bucket, wired through one MockTransport."""

    def __init__(self, monkeypatch, package: Dict[str, Any]):
        self.worker = _load_worker()
        self.package = package
        self.ledger = mrs.MarketingRunService(supabase=FakeSupabase())
        self.fake = self.ledger.sb
        self.server = TestClient(app)  # no lifespan: its jobs would reach Supabase
        self.calls: List[tuple] = []
        self.stored: Dict[str, bytes] = {}
        #: optional hook: (request) -> Optional[httpx.Response]; runs BEFORE forwarding
        self.before = None
        #: optional hook: (request, response) -> response; runs AFTER forwarding
        self.after = None
        monkeypatch.setattr(settings, "MARKETING_WORKER_TOKEN", _TOKEN)
        monkeypatch.setattr(mrs.settings, "MARKETING_RUN_STALE_SECONDS", 2700)
        monkeypatch.setattr(mrs.settings, "MARKETING_MAX_RUN_ATTEMPTS", 6)
        monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", False)
        monkeypatch.setattr(mod, "get_marketing_run_service", lambda: self.ledger)

        async def _no_writer(*_a, **_k):  # the accepted package is seeded; the model is never reached
            raise AssertionError("the writer must not run in this test")

        self.scripts = ss.MarketingScriptService(self.ledger, writer=_no_writer)
        monkeypatch.setattr(mod, "get_marketing_script_service", lambda: self.scripts)
        # the voice child + encode, the render's ffmpeg + download
        from marketing import render as rd
        from marketing import voice as vc

        monkeypatch.setattr(vc, "run_child", _narration)
        monkeypatch.setattr(vc, "encode_m4a", lambda wav, out: (b"e2e-m4a:" + wav.name.encode(), 2.0))
        monkeypatch.setattr(rd, "download", self._download)
        monkeypatch.setattr(rd, "produce_video", self._produce_video)
        self.renders = 0
        transport = httpx.MockTransport(self.handler)

        class Shim:
            Client = functools.partial(httpx.Client, transport=transport)
            TransportError = httpx.TransportError

        w = self.worker
        monkeypatch.setattr(w, "httpx", Shim)
        monkeypatch.setattr(w, "ffmpeg_version", lambda: "ffmpeg version 5.1.9 (e2e)")
        monkeypatch.setattr(w, "voice_readiness", lambda d: {"ready": True, "problems": []})
        monkeypatch.setattr(w, "render_readiness", lambda d: {"ready": True, "problems": [], "raqm": True})

        class _Fast:
            monotonic = staticmethod(__import__("time").monotonic)

            @staticmethod
            def sleep(_s):
                return None

        monkeypatch.setattr(w, "time", _Fast())
        today = mrs.run_date_et(datetime.now(timezone.utc))
        monkeypatch.setenv("MARKETING_API_BASE_URL", f"https://{_BACKEND}")
        monkeypatch.setenv("MARKETING_WORKER_TOKEN", _TOKEN)
        monkeypatch.setenv("MARKETING_FORCE", "1")
        monkeypatch.setenv("MARKETING_RUN_DATE", today.isoformat())
        monkeypatch.setenv("MARKETING_FONTS_DIR", str(_PKG / "assets" / "fonts"))
        monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)

    # ── the fakes behind the transport ─────────────────────────────────────────

    def _download(self, url: str, **_k) -> bytes:
        path = url.split(f"/{settings.MARKETING_MEDIA_BUCKET}/", 1)[1]
        return self.stored[path]

    def _produce_video(self, *, workdir, specs, words, narration_seconds, audio_file, fonts_dir,
                       logo_path, threads, heartbeat, run_id, max_seconds, layout_engine):
        from marketing import voice as vc

        self.renders += 1
        assert (Path(workdir) / audio_file).read_bytes().startswith(b"e2e-m4a:")
        return b"e2e-mp4:" + str(len(specs)).encode(), narration_seconds + vc.DISCLAIMER_CARD_SECONDS, \
            list(range(len(specs)))

    def _seed_script(self, run_id: str) -> None:
        if self.fake.tables[mrs.SCRIPTS].rows:
            return
        self.fake.tables[mrs.SCRIPTS].rows.append({
            "run_id": run_id, "status": "accepted", "run_date": self._run_row()["run_date"],
            "source_ref": "journey:mr_market", "template_id": "case_story", "generation_id": "g-1",
            "output": dict(self.package), "fact_sheet": {}, "violations": [], "generations": 1,
            "tokens_used": 0, "content_rejections": 0,
        })

    def _run_row(self) -> Dict[str, Any]:
        (row,) = self.fake.tables[mrs.RUNS].rows
        return row

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.host, request.url.path))
        if self.before is not None:
            early = self.before(request)
            if early is not None:
                return early
        if request.url.host == "sb.example":
            response = self._storage_put(request)
        else:
            assert request.url.host == _BACKEND, request.url
            if request.method == "POST" and request.url.path.endswith("/script"):
                self._seed_script(request.url.path.rsplit("/", 2)[-2])
            r = self.server.request(request.method, request.url.path, content=request.content,
                                    headers={k: v for k, v in request.headers.items() if k.lower() != "host"})
            response = httpx.Response(r.status_code, content=r.content,
                                      headers={"content-type": "application/json"})
        if self.after is not None:
            response = self.after(request, response)
        return response

    def _storage_put(self, request: httpx.Request) -> httpx.Response:
        """A signed-upload PUT: the multipart part the worker sent becomes the object, with ITS size
        and content type — what the server's verification reads back."""
        assert request.method == "PUT" and request.headers.get("x-upsert") == "false"
        path = request.url.path.split(f"/upload/sign/{settings.MARKETING_MEDIA_BUCKET}/", 1)[1]
        if path in self.fake.objects:
            return httpx.Response(409, json={"statusCode": "409", "error": "Duplicate"})
        msg = email.message_from_bytes(
            b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.read())
        (part,) = msg.get_payload()
        data = part.get_payload(decode=True)
        self.stored[path] = data
        self.fake.objects.add(path)
        self.fake.object_meta[path] = {"size": len(data), "mimetype": part.get_content_type()}
        return httpx.Response(200, json={"Key": path})

    # ── views ──────────────────────────────────────────────────────────────────

    def assets(self, kind=None) -> List[Dict[str, Any]]:
        return [a for a in self.fake.tables[mrs.ASSETS].rows if kind is None or a["kind"] == kind]

    def posts(self) -> List[Dict[str, Any]]:
        return list(self.fake.tables[mrs.POSTS].rows)


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch, _PACKAGE)


def _ready(world, kind):
    return [a for a in world.assets(kind) if a["status"] == "ready"]


def test_a_first_tick_goes_from_claim_to_media_ready_through_the_real_app(world):
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready"
    (manifest,), (audio,), (video,) = _ready(world, "manifest"), _ready(world, "audio"), _ready(world, "video")
    # The pointers ride on the run, and the server verifies them on the read-back.
    assert run["metadata"]["voice_asset_id"] == audio["id"]
    assert run["metadata"]["video_asset_id"] == video["id"]
    # The server accepted what the video declared it drew: the real cards.py strings against the
    # real run_service check (the accepted cards, the disclaimer card, the end card).
    drawn = video["metadata"]["onscreen_text"]
    assert _PACKAGE["disclaimer_card"] in drawn and "The partner" in drawn and "caydexinvest.com" in drawn
    assert video["metadata"]["voice_asset_id"] == audio["id"]
    # Posts: captions from the ACCEPTED package, media posts held for review.
    posts = {(p["platform"], p["format"]): p for p in world.posts()}
    assert set(posts) == {("tiktok", "video"), ("x", "text")}
    assert posts[("tiktok", "video")]["asset_ids"] == [video["id"]]
    assert posts[("tiktok", "video")]["caption"] == "server TikTok copy"
    assert all(p["status"] == "pending_review" and p["metadata"]["dry_run"] is True for p in posts.values())
    assert world.renders == 1
    # Every call after the claim presented it; nothing went to a host but the two expected.
    assert {h for _m, h, _p in world.calls} == {_BACKEND, "sb.example"}


def test_a_lost_claim_response_is_recognised_as_our_own_claim(world):
    lost = {"done": False}

    def lose_claim(request, response):
        if request.url.path.endswith("/runs/claim") and not lost["done"]:
            lost["done"] = True     # committed on the server; the worker never hears back
            raise httpx.ReadTimeout("lost after commit", request=request)
        return response

    world.after = lose_claim
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["attempts"] == 1


def test_an_upload_whose_response_was_lost_is_finished_from_the_bucket_on_the_next_tick(world):
    """The audio PUT lands but its response is lost: the tick fails. The next tick re-claims the
    run and re-registers the same bytes — the server finds the object ALREADY in the bucket,
    verifies its size and type (the branch that used to skip verification), and marks it ready
    without a second upload."""
    state = {"lost": False}

    def lose_audio_put(request, response):
        if request.url.host == "sb.example" and "/audio-" in request.url.path and not state["lost"]:
            state["lost"] = True
            raise httpx.ReadTimeout("PUT landed, response lost", request=request)
        return response

    world.after = lose_audio_put
    assert world.worker.main() == 1
    run = world._run_row()
    assert run["status"] == "failed"
    world.after = None
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["attempts"] == 2
    audio_puts = [c for c in world.calls if c[1] == "sb.example" and "/audio-" in c[2]]
    assert len(audio_puts) == 1                     # finished from the bucket, never re-uploaded
    assert len(_ready(world, "audio")) == 1 and len(_ready(world, "video")) == 1


def test_a_resume_after_voiced_neither_voices_nor_renders_what_it_already_has(world, monkeypatch):
    """Tick 1 dies in the render; tick 2 resumes after `voiced` from the server's read-back."""
    from marketing import render as rd

    def boom(**_k):
        raise RuntimeError("container killed mid-render")

    monkeypatch.setattr(rd, "produce_video", boom)
    assert world.worker.main() == 1
    assert world._run_row()["stage"] == "voiced"
    monkeypatch.setattr(rd, "produce_video", world._produce_video)
    assert world.worker.main() == 0
    assert world._run_row()["status"] == "media_ready"
    assert len(world.assets("audio")) == 1 and world.renders == 1


def test_a_zombie_tick_is_refused_by_the_claim_fence_and_writes_nothing(world):
    """Another tick re-claims the run underneath this one (attempts and nonce move on). Every
    write of the old tick is refused 409 MARKETING_RUN_NOT_HELD; it exits 0 without a failure
    write over the new holder."""
    state = {"stolen": False}

    def steal(request):
        if request.method == "PATCH" and not state["stolen"]:
            state["stolen"] = True
            row = world._run_row()
            row["attempts"] = int(row["attempts"]) + 1
            row["metadata"] = {**row["metadata"], "claim_nonce": "ab" * 16}
        return None

    world.before = steal
    assert world.worker.main() == 0
    row = world._run_row()
    assert row["status"] == "in_progress" and row["attempts"] == 2 and not row.get("last_error")
    assert world.posts() == []


def test_a_script_the_judge_did_not_enforce_is_voiced_rendered_and_never_posted(monkeypatch):
    world = World(monkeypatch, {**_PACKAGE, "judge": {"mode": "shadow", "verdicts": []}})
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "skipped" and run["metadata"]["skip_reason"] == "judge_not_enforced"
    assert world.posts() == []


def test_a_video_declaring_text_the_script_does_not_carry_is_refused_by_the_server(world, monkeypatch):
    """The worker is the least-trusted process: if it drew (and declared) a string that is not the
    accepted script's, the real server refuses the video and nothing is posted."""
    from marketing import cards

    real = cards.onscreen_strings
    monkeypatch.setattr(cards, "onscreen_strings",
                        lambda spec: real(spec) + (["Buy now"] if spec.kind == "text" else []))
    assert world.worker.main() == 1
    run = world._run_row()
    assert run["status"] == "failed" and "MARKETING_REQUEST_INVALID" in (run["last_error"] or "")
    assert world.assets("video") == [] and world.posts() == []


def test_a_text_only_day_makes_no_media_and_still_records_its_posts(monkeypatch):
    """Not even a narration: and so a narration fault — here one far over the video budget —
    can no longer cost a day whose outlets are all text (review 2026-09-29)."""
    from marketing import voice as vc

    world = World(monkeypatch, {**_PACKAGE, "posts": {"x": _PACKAGE["posts"]["x"]}})

    def too_long(lines, **k):
        raise AssertionError("a text-only day must never be narrated")

    monkeypatch.setattr(vc, "run_child", too_long)
    assert world.worker.main() == 0
    assert world._run_row()["status"] == "media_ready"
    assert world.assets("audio") == [] and world.assets("video") == [] and world.renders == 0
    assert [(p["platform"], p["format"]) for p in world.posts()] == [("x", "text")]
