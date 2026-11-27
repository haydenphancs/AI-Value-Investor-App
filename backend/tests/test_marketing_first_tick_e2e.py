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

The drop-1 image day (the tests at the end) fakes nothing more: its accepted output is the real
`script_service.freeze_post_formats`, the 4:5 JPEG is the real `cards.render_image`, and the
publisher's picture loader, the Bluesky/X `prepare` and the review bundle's image pick read the
rows the worker left.

The template days (drop 2a, then drop 2b) fake only the company-news source (a stub with the adapter's
public surface) and Storage's logo upload. A 2b day pins the ET date to a day the REAL calendar runs the
series on (the worker's MARKETING_RUN_DATE, the claim window and the kick's hold check), lists every
series in MARKETING_NEWS_SERIES, and goes claim → accepted template → voiced → rendered (video + image in
the series' own layout) → posts pending review; switching the series off before the posts skips the day.

The Batch 2 days (the tests at the very end) turn MARKETING_VIDEO_FEATURES on: a lesson's package is stored
through the REAL write-time freeze (`_frozen_output`), a template's by the real build, and the render is the
REAL `render.produce_video` — real card PNGs, captions and timeline — with only its ffmpeg call faked. Since
the fix round (2026-10-20) the data card lands on the card the read-back's `data_card_slot` names (the kept
hedge cards stay on screen), and a hook that passes only beside its first line never becomes a hook card.

The Batch 3 days list facebook and linkedin in MARKETING_VIDEO_EXTRA_PLATFORMS: the REAL write
freezes the extra video posts, the UNCHANGED worker never sees them and names only the primary posts, and the real
server's create_posts appends the two video posts itself; with the switch empty (pinned by `World`, as the other two
Batch 3 switches) the day is the switch-on day minus exactly the extras.

The spotlight days (migration 210, the last tests) drive the worker's tick plan on a frozen ET clock: with the lane's
switch off (pinned by `World`) the second claim of a day answers `not_scheduled` and nothing more is written; with it
on, a Monday's 10:15 tick runs the spotlight run (Money Map — the single-company pair, shipped in news-v22, is not
listed in the per-series switch: its default) and the
16:15 tick the main run (CEO Buys), each under its own lane's storage paths and post keys, never sharing one.

The post-art days (workstream C, the very last tests) turn MARKETING_ART_ENABLED on with the post-art suite's fake art
clients: the REAL build makes and stores the art, the REAL worker downloads it by its content address and draws it —
on the spotlight's `feature` picture band AND its video cover, on a main-lane Money Map's cover only — and declares it;
the REAL server accepts exactly that, refuses a forged declaration 422, and create_posts notes it (the review line
reads the note). With the switch off the day is the day of before, byte for byte — also when it goes off between the
build and the read-back the render keeps (a real kill switch).
"""

from __future__ import annotations

import email
import functools
import importlib.util
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

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
_ET = ZoneInfo("America/New_York")

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
    """The worker + the real app + the fake database/bucket, wired through one MockTransport.

    Batch 2: `audio_seconds` is the narration's encoded length (the fake encode's answer; long enough
    for every card of a real timeline when the render is real), and `freeze_on_seed` stores the seeded
    package the way the REAL write path stores an accepted one — `MarketingScriptService._frozen_output`,
    reading MARKETING_VIDEO_FEATURES and the holder's claim (the real worker's declared capabilities)
    from the run row — instead of verbatim."""

    def __init__(self, monkeypatch, package: Dict[str, Any], *, audio_seconds: float = 2.0,
                 freeze_on_seed: bool = False):
        self.worker = _load_worker()
        self.package = package
        self.freeze_on_seed = freeze_on_seed
        self.ledger = mrs.MarketingRunService(supabase=FakeSupabase())
        self.fake = self.ledger.sb
        self.server = TestClient(app)  # no lifespan: its jobs would reach Supabase
        self.calls: List[tuple] = []
        self.stored: Dict[str, bytes] = {}
        #: the keywords each faked produce_video call carried beyond drop 1's (Batch 2: hook_card, push_in)
        self.openings: List[Dict[str, Any]] = []
        #: optional hook: (request) -> Optional[httpx.Response]; runs BEFORE forwarding
        self.before = None
        #: optional hook: (request, response) -> response; runs AFTER forwarding
        self.after = None
        monkeypatch.setattr(settings, "MARKETING_WORKER_TOKEN", _TOKEN)
        monkeypatch.setattr(mrs.settings, "MARKETING_RUN_STALE_SECONDS", 2700)
        monkeypatch.setattr(mrs.settings, "MARKETING_MAX_RUN_ATTEMPTS", 6)
        monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", False)
        # Batch 2: the fail-closed default, pinned (never the .env's); a feature test turns it on.
        monkeypatch.setattr(settings, "MARKETING_VIDEO_FEATURES", "")
        # Batch 3: the three new switches at their declared defaults (no extra video post, the four posting days —
        # the kick's `_select` reads them, no trial Reels — the Instagram request reads it at send time), whatever
        # this machine's environment says; a Batch 3 test turns one on.
        monkeypatch.setattr(settings, "MARKETING_VIDEO_EXTRA_PLATFORMS", "")
        monkeypatch.setattr(settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat")
        monkeypatch.setattr(settings, "MARKETING_INSTAGRAM_TRIAL_MODE", "off")
        # Migration 210: the spotlight lane at its declared defaults (off, so its claim answers not_scheduled), and
        # the worker's lane variables unset (both lanes, the default spotlight hour), whatever this machine says.
        monkeypatch.setattr(settings, "MARKETING_SPOTLIGHT_ENABLED", False)
        monkeypatch.setattr(settings, "MARKETING_SPOTLIGHT_WEEKDAYS", "tue,thu,sat")
        monkeypatch.setattr(settings, "MARKETING_SPOTLIGHT_HOUR_ET", 10)
        monkeypatch.setattr(settings, "MARKETING_RUN_HOUR_ET", 16)
        monkeypatch.delenv("MARKETING_RUN_LANE", raising=False)
        monkeypatch.delenv("MARKETING_SPOTLIGHT_HOUR_ET", raising=False)
        monkeypatch.delenv("MARKETING_RUN_HOUR_ET", raising=False)
        monkeypatch.setattr(mod, "get_marketing_run_service", lambda: self.ledger)

        async def _no_writer(*_a, **_k):  # the accepted package is seeded; the model is never reached
            raise AssertionError("the writer must not run in this test")

        self.scripts = ss.MarketingScriptService(self.ledger, writer=_no_writer)
        monkeypatch.setattr(mod, "get_marketing_script_service", lambda: self.scripts)
        # the voice child + encode, the render's ffmpeg + download
        from marketing import render as rd
        from marketing import voice as vc

        monkeypatch.setattr(vc, "run_child", _narration)
        monkeypatch.setattr(vc, "encode_m4a", lambda wav, out: (b"e2e-m4a:" + wav.name.encode(), audio_seconds))
        monkeypatch.setattr(rd, "download", self._download)
        #: the REAL render.produce_video (cards → PNGs, captions, timeline, then ONE ffmpeg call), for a
        #: test that fakes only that ffmpeg call (`_FfmpegFake`)
        self.real_produce_video = rd.produce_video
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
                       logo_path, threads, heartbeat, run_id, max_seconds, layout_engine, **opening):
        from marketing import voice as vc

        self.renders += 1
        # Drop 1's keywords are named above; anything else (Batch 2's hook_card / push_in) is recorded, so a
        # day without frozen features can assert drop 1's call exactly (`openings == [{}]`).
        self.openings.append(dict(opening))
        assert (Path(workdir) / audio_file).read_bytes().startswith(b"e2e-m4a:")
        return b"e2e-mp4:" + str(len(specs)).encode(), narration_seconds + vc.DISCLAIMER_CARD_SECONDS, \
            list(range(len(specs)))

    def _seed_script(self, run_id: str) -> None:
        if self.fake.tables[mrs.SCRIPTS].rows:
            return
        output = dict(self.package)
        if self.freeze_on_seed:
            import asyncio
            from datetime import date

            # What `_finish` stores for an accepted lesson: the REAL write-time freeze (formats, then the
            # video features MARKETING_VIDEO_FEATURES lists that the holder's claim says it can draw).
            output = asyncio.run(self.scripts._frozen_output(
                output, date.fromisoformat(self._run_row()["run_date"]), run_id=run_id, gen_id="g-1",
                image_posts=False))
        self.fake.tables[mrs.SCRIPTS].rows.append({
            "run_id": run_id, "status": "accepted", "run_date": self._run_row()["run_date"],
            "source_ref": "journey:mr_market", "template_id": "case_story", "generation_id": "g-1",
            "output": output, "fact_sheet": {}, "violations": [], "generations": 1,
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


# ── drop 1: an image day, through every seam (contract C3 → C5 → C6 → C7 → C9/C10) ──────────────
# The accepted output is the REAL `script_service.freeze_post_formats` of a writer package with an
# `image_post` (what `_frozen_output` stores with MARKETING_IMAGE_POSTS on); the worker renders the
# REAL 4:5 JPEG (cards.render_image, Pillow + the vendored Inter); the REAL server checks what it
# declares it drew and records the image posts; then the publisher's picture loader, the adapters'
# `prepare` and the review bundle's image pick read the rows the worker actually left.

_IMAGE_POST = {
    "title": "Your moody business partner",
    "paragraphs": ["Every day he names a price for your share of the business.",
                   "His price follows his mood, not what the business earns."],
}
_SB_PUBLIC = "https://sb.example"


def _image_day(package: Dict[str, Any], *, x_images: bool = False) -> Dict[str, Any]:
    run_date = mrs.run_date_et(datetime.now(timezone.utc))
    return ss.freeze_post_formats({**package, "image_post": dict(_IMAGE_POST)}, run_date,
                                  image_posts=True, x_images=x_images)


_IMAGE_DAY_POSTS = {
    **_PACKAGE["posts"],
    "bluesky": {"platform": "bluesky", "title": None, "caption": "server Bluesky copy"},
    "threads": {"platform": "threads", "title": None, "caption": "server Threads copy"},
}


@pytest.fixture
def image_world(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", _SB_PUBLIC)
    return World(monkeypatch, _image_day({**_PACKAGE, "posts": _IMAGE_DAY_POSTS}))


def _the_card(world) -> Dict[str, Any]:
    (card,) = [a for a in _ready(world, "card") if (a.get("metadata") or {}).get("image_role") == "post_image"]
    return card


def _serve_bucket(world, monkeypatch) -> None:
    """The public bucket for the publisher's picture download: GET <public url> → the stored bytes."""
    from app.services.marketing import outlet_base

    prefix = f"/storage/v1/object/public/{settings.MARKETING_MEDIA_BUCKET}/"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path.startswith(prefix), request.url
        data = world.stored.get(request.url.path[len(prefix):])
        return httpx.Response(200, content=data) if data is not None else httpx.Response(404)

    monkeypatch.setattr(outlet_base, "_fetch_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(mrs, "get_marketing_run_service", lambda: world.ledger)


def test_an_image_day_goes_from_the_frozen_script_to_image_posts_carrying_the_verified_card(image_world):
    import hashlib
    import io

    from PIL import Image

    world = image_world
    frozen = world.package
    assert frozen["post_formats"] == {"tiktok": "video", "x": "text", "bluesky": "image", "threads": "image"}
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready"
    card, (video,) = _the_card(world), _ready(world, "video")
    # The pointers ride on the run (the rendered checkpoint), and the server verifies them.
    assert run["metadata"]["image_asset_id"] == card["id"] and run["metadata"]["video_asset_id"] == video["id"]
    # What the card declares it drew: the accepted title + paragraphs + the code-owned footer, verbatim.
    # news-v23: then the kicker pill's word and the brand row's (the server's LESSON_BRAND_TEXT), once each.
    assert card["metadata"]["onscreen_text"] == [_IMAGE_POST["title"], *_IMAGE_POST["paragraphs"],
                                                 frozen["image_footer"], "INVESTING LESSON", "Caydex"]
    assert card["content_type"] == "image/jpeg" and card["storage_path"].endswith(".jpg")
    # The object in the bucket IS that row: its size, its sha256, a 1080×1350 baseline JPEG.
    data = world.stored[card["storage_path"]]
    assert len(data) == card["bytes"] <= 950_000 and hashlib.sha256(data).hexdigest() == card["sha256"]
    with Image.open(io.BytesIO(data)) as im:
        assert im.format == "JPEG" and im.size == (1080, 1350) and not im.info.get("progressive")
    # Posts: each platform in its FROZEN format; image posts carry exactly the verified card.
    posts = {(p["platform"], p["format"]): p for p in world.posts()}
    assert set(posts) == {("tiktok", "video"), ("x", "text"), ("bluesky", "image"), ("threads", "image")}
    assert posts[("tiktok", "video")]["asset_ids"] == [video["id"]]
    assert posts[("bluesky", "image")]["asset_ids"] == [card["id"]] == posts[("threads", "image")]["asset_ids"]
    assert not posts[("x", "text")].get("asset_ids")
    assert posts[("bluesky", "image")]["caption"] == "server Bluesky copy"       # captions unchanged (C7)
    assert all(p["status"] == "pending_review" for p in posts.values())
    assert world.renders == 1


@pytest.mark.asyncio
async def test_what_the_worker_registered_is_what_the_publisher_and_the_review_bundle_read(image_world,
                                                                                          monkeypatch):
    """The downstream half of the seam, on the rows the REAL worker left: the publisher resolves the
    picture from the ledger (ready card of the run, the accepted script's words for its alt text),
    downloads it from its public URL and matches the sha256; Bluesky builds its images embed from it;
    the review bundle picks the same card."""
    from app.services.marketing import outlet_base, outlet_bluesky, publisher_service, review_service

    world = image_world
    assert world.worker.main() == 0
    card = _the_card(world)
    _serve_bucket(world, monkeypatch)
    bluesky_post = next(p for p in world.posts() if p["platform"] == "bluesky")

    row, problem = await publisher_service._with_image(bluesky_post)
    assert problem is None
    image = outlet_base.image_of(row)
    assert image is not None and image.asset_id == card["id"] and image.sha256 == card["sha256"]
    assert image.size == card["bytes"] and image.url == mrs.MarketingRunService.public_url(card["storage_path"])
    assert (image.title, list(image.paragraphs)) == (_IMAGE_POST["title"], _IMAGE_POST["paragraphs"])
    assert await outlet_base.fetch_post_image(image.url, size=image.size, sha256=image.sha256) \
        == world.stored[card["storage_path"]]

    prepared = outlet_bluesky.ADAPTER.prepare(row)
    embed = prepared.payload["record"]["embed"]
    assert embed["$type"] == "app.bsky.embed.images"
    (entry,) = embed["images"]
    assert entry["alt"] == image.alt(2000) and entry["alt"].startswith(_IMAGE_POST["title"])
    assert entry["aspectRatio"] == {"width": 1080, "height": 1350}
    assert prepared.payload["image"]["sha256"] == card["sha256"]
    assert outlet_base.POST_IMAGE_KEY not in bluesky_post        # the picture rides only on prepare's copy

    run = world._run_row()
    image_posts = [p for p in world.posts() if p["format"] == "image"]
    picked = review_service._pick_image(run["id"], run, world.assets(), image_posts)
    assert picked is not None and picked["id"] == card["id"]


def test_an_image_only_day_is_never_narrated_and_x_takes_the_image_when_its_switch_was_on(monkeypatch):
    from marketing import voice as vc

    monkeypatch.setattr(settings, "SUPABASE_URL", _SB_PUBLIC)
    posts = {"x": _PACKAGE["posts"]["x"], "bluesky": _IMAGE_DAY_POSTS["bluesky"]}
    world = World(monkeypatch, _image_day({**_PACKAGE, "posts": posts}, x_images=True))
    assert world.package["post_formats"] == {"x": "image", "bluesky": "image"}

    def never(lines, **k):
        raise AssertionError("an image-only day must never be narrated")

    monkeypatch.setattr(vc, "run_child", never)
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready"
    assert world.assets("audio") == [] and world.assets("video") == [] and world.renders == 0
    card = _the_card(world)
    assert sorted((p["platform"], p["format"], tuple(p["asset_ids"])) for p in world.posts()) == [
        ("bluesky", "image", (card["id"],)), ("x", "image", (card["id"],))]


@pytest.mark.asyncio
async def test_an_x_image_post_prepares_with_its_alt_text_only_while_x_images_is_on(monkeypatch):
    from app.services.marketing import outlet_x, publisher_service
    from app.services.marketing.outlet_base import MarketingPublishRefused

    monkeypatch.setattr(settings, "SUPABASE_URL", _SB_PUBLIC)
    world = World(monkeypatch, _image_day({**_PACKAGE, "posts": {"x": _PACKAGE["posts"]["x"]}}, x_images=True))
    assert world.worker.main() == 0
    _serve_bucket(world, monkeypatch)
    (x_post,) = world.posts()
    row, problem = await publisher_service._with_image(x_post)
    assert problem is None
    monkeypatch.setattr(settings, "MARKETING_X_IMAGES", True)
    monkeypatch.setattr(settings, "MARKETING_X_MEDIA_UPLOAD_MICROS", 15_000)   # pinned, never the .env's
    prepared = outlet_x.ADAPTER.prepare(row)
    assert prepared.payload["image"]["asset_id"] == _the_card(world)["id"]
    assert prepared.payload["image"]["alt"].startswith(_IMAGE_POST["title"])
    # The media upload and the alt text are billed apart from the post, in the order the send makes the
    # calls, and journaled in ONE pre-send write (Batch 1, 2026-10-15).
    assert [op for op, _micros in prepared.pre_send_charges] == [outlet_x.MEDIA_UPLOAD_OP, outlet_x.MEDIA_ALT_OP]
    # Turned off between the freeze and the publish: the image post is refused, never sent as text.
    monkeypatch.setattr(settings, "MARKETING_X_IMAGES", False)
    with pytest.raises(MarketingPublishRefused, match="MARKETING_X_IMAGES"):
        outlet_x.ADAPTER.prepare(row)


def test_a_post_image_declaring_text_the_script_does_not_carry_is_refused_by_the_server(image_world,
                                                                                         monkeypatch):
    """The image twin of the video check: a worker that declared (and so drew) a string that is not
    the accepted image post's is refused by the real server — no card, no posts."""
    from marketing import cards

    real = cards.image_onscreen_strings
    monkeypatch.setattr(cards, "image_onscreen_strings", lambda spec: real(spec) + ["Buy now"])
    world = image_world
    assert world.worker.main() == 1
    run = world._run_row()
    assert run["status"] == "failed" and "MARKETING_REQUEST_INVALID" in (run["last_error"] or "")
    assert _ready(world, "card") == [] and world.posts() == []


def test_a_resume_after_a_lost_video_upload_reuses_the_ready_post_image(image_world):
    """Tick 1 registers the image, then the video PUT's response is lost (the tick fails). Tick 2
    finds the image READY with the same render key in the server's read-back and reuses it: one card
    object, one card PUT, and the image posts carry it."""
    world = image_world
    state = {"lost": False}

    def lose_video_put(request, response):
        if request.url.host == "sb.example" and "/video-" in request.url.path and not state["lost"]:
            state["lost"] = True
            raise httpx.ReadTimeout("PUT landed, response lost", request=request)
        return response

    world.after = lose_video_put
    assert world.worker.main() == 1
    assert world._run_row()["status"] == "failed" and len(_ready(world, "card")) == 1
    world.after = None
    registered: List[str] = []

    def record_kind(request):
        if request.method == "POST" and request.url.host == _BACKEND and request.url.path.endswith("/assets"):
            registered.append(json.loads(request.content)["kind"])
        return None

    world.before = record_kind
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["attempts"] == 2
    card = _the_card(world)
    assert len(world.assets("card")) == 1
    assert len([c for c in world.calls if c[1] == "sb.example" and "/card-" in c[2]]) == 1
    # Reused by its render key, never re-registered: the server's content-addressed row would hide a
    # re-render of the same bytes — this is what proves the reuse.
    assert "card" not in registered and "video" in registered
    assert {tuple(p["asset_ids"]) for p in world.posts() if p["format"] == "image"} == {(card["id"],)}


# ══ Drop 2a (contract D16): a TEMPLATE day — the real worker against the real app ════════════════════
#
# Nothing new is faked but the company-news source (a stub with the adapter's public surface: one CEO
# Buys week of today's window and its two logos) and Storage's logo upload. The script is NOT seeded:
# the first kick runs the REAL template build — the real records, templates, re-check, logo check and
# store, frozen formats — and the worker then voices the per-line script, renders the opening-card
# video and the template post image (the real news_layouts, logos fetched from the bucket by their
# sha), the REAL server checks what each declares it drew, and create_posts re-checks the template
# before recording the posts, every one held for review.


class _StubNews:
    """`company_news_adapter`'s public surface: `candidates`, `fetch_logo`, `MarketingNewsUnavailable`."""

    class MarketingNewsUnavailable(Exception):
        def __init__(self, series: str, reason: str = "internal_error", detail: str = "") -> None:
            super().__init__(f"{series}: {reason}")
            self.series, self.reason, self.detail = series, reason, detail

    def __init__(self, run_date) -> None:
        from types import SimpleNamespace

        from app.services.marketing import company_news_rules as R

        start, end = R.insider_window(run_date)

        def row(sym, name, amount, shares, filed):
            return R.InsiderPurchase(company=R.CompanyRef(symbol=sym, name=name), role="ceo", person_name=None,
                                     amount_usd=amount, shares=shares, purchases=1, earliest_trade_date=filed,
                                     latest_trade_date=filed, filing_dates=(filed,), holding="direct",
                                     amended=False)

        self.week = R.InsiderBuysWeek(series="ceo_buys", window_start=start, window_end=end, rows=(
            row("LOW", "Lowe's", 2_300_000.0, 9_812.0, end - __import__("datetime").timedelta(days=3)),
            row("SBUX", "Starbucks", 410_000.0, 4_850.0, end - __import__("datetime").timedelta(days=2)),
            # owner decisions 2026-11-20 ("3 - 5 companies at once"): a Form 4 week holds three companies at least
            row("NKE", "Nike", 250_000.0, 3_500.0, end - __import__("datetime").timedelta(days=1))))
        self.answer = SimpleNamespace(series="ceo_buys", records=(self.week,), skip_reason=None, rejections={})
        self.calls: List[str] = []
        self.logo_calls: List[str] = []

    async def candidates(self, series, *, run_date, exclude, limit, deadline, **_kw):
        self.calls.append(series)
        assert series == "ceo_buys", series
        return self.answer

    async def fetch_logo(self, symbol, *, max_bytes, timeout):
        self.logo_calls.append(symbol)
        return _logo_png(symbol), "image/png"


def _noted_news() -> "_StubNews":
    """The default CEO-buys stub with its second row held INDIRECTLY: the roundup's picture then carries a NOTE ("Some
    of the shares …") that its video cover does not draw. Since 2026-11-20 a Form 4 roundup's cover IS its picture's
    headline and rows, so only such a string still tells a drawn picture (a data card) from the cover."""
    import dataclasses

    news = _StubNews(mrs.run_date_et(datetime.now(timezone.utc)))
    rows = (news.week.rows[0], dataclasses.replace(news.week.rows[1], holding="indirect"), *news.week.rows[2:])
    news.week = dataclasses.replace(news.week, rows=rows)
    news.answer.records = (news.week,)
    return news


def _logo_png(symbol: str) -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    shade = sum(symbol.encode()) % 200
    width, height = 160, 120
    raw = b"".join(b"\x00" + bytes([shade, 60, 120]) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class _LogoBucket:
    """The fake bucket plus `upload` (the web side stores logos itself — no signed URL, no asset row):
    `x-upsert: false`, recorded where the listing and the worker's download read it."""

    def __init__(self, world: "TemplateWorld", inner: Any) -> None:
        self.world, self.inner = world, inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def upload(self, path: str, data: bytes, options: Dict[str, Any]) -> Dict[str, Any]:
        assert options.get("upsert") == "false" and options.get("cache-control") == "31536000"
        if path in self.world.fake.objects:
            raise RuntimeError("{'statusCode': 409, 'error': Duplicate}")
        self.world.fake.objects.add(path)
        self.world.fake.object_meta[path] = {"size": len(data), "mimetype": options["content-type"]}
        self.world.stored[path] = bytes(data)
        self.world.logo_uploads.append(path)
        return {"path": path}


class _LogoStorage:
    def __init__(self, world: "TemplateWorld", inner: Any) -> None:
        self.world, self.inner = world, inner

    def from_(self, bucket: str) -> _LogoBucket:
        return _LogoBucket(self.world, self.inner.from_(bucket))


class TemplateWorld(World):
    """`World` whose script is built by the REAL template flow (never seeded).

    Default: today (whatever weekday the suite runs on) is planned as a Monday and the stub answers CEO
    Buys. With `run_date` (drop 2b), the ET date is PINNED to that day on both sides — the worker's
    MARKETING_RUN_DATE, the claim window, the kick's hold check — and the REAL calendar plans it, so a
    2b day is exactly the chain production walks on that date; `news` answers it."""

    def __init__(self, monkeypatch, *, news: Any = None, run_date: Any = None,
                 news_series: str = "ceo_buys,insider_buys,thirteen_f,money_map",
                 audio_seconds: float = 2.0) -> None:
        from app.services.marketing import selection
        from marketing import render as rd

        monkeypatch.setattr(settings, "SUPABASE_URL", _SB_PUBLIC)
        super().__init__(monkeypatch, {}, audio_seconds=audio_seconds)
        self.logo_uploads: List[str] = []
        self.downloads: List[str] = []
        self.video_calls: List[Dict[str, Any]] = []
        self.fake.storage = _LogoStorage(self, self.fake.storage)
        if run_date is not None:
            monkeypatch.setattr(mod, "run_date_et", lambda now=None: run_date)
            monkeypatch.setattr(mrs, "run_date_et", lambda now=None: run_date)
            monkeypatch.setattr(ss, "_today_et", lambda: run_date)
            monkeypatch.setenv("MARKETING_RUN_DATE", run_date.isoformat())
        self.run_date = run_date or mrs.run_date_et(datetime.now(timezone.utc))
        self.news = news if news is not None else _StubNews(self.run_date)
        self.scripts = ss.MarketingScriptService(self.ledger, writer=self._never_the_writer, news=self.news)
        monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
        monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", news_series)
        monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_POSTS", True)
        monkeypatch.setattr(mrs.settings, "MARKETING_X_IMAGES", False)
        monkeypatch.setattr(mrs.settings, "MARKETING_X_ALLOW_URLS", False)
        # news-v22: the first-line prefixes at their declared default (off), whatever this machine's environment says
        monkeypatch.setattr(mrs.settings, "MARKETING_FIRST_LINE_PREFIX", False)
        if run_date is None:
            # Today, whatever weekday the suite runs on, is a Monday: CEO Buys first, the lesson last.
            monkeypatch.setattr(selection, "plan_for", lambda d: selection.DayPlan(
                False, ("ceo_buys", "insider_buys", "money_map", "theme_explainer", selection.LESSON), "monday"))
        monkeypatch.setattr(rd, "produce_video", self._produce_template_video)

    @staticmethod
    async def _never_the_writer(*_a, **_k):
        raise AssertionError("a template day never calls the writer")

    def _seed_script(self, run_id: str) -> None:
        return None     # the real template build writes the day's script

    def _download(self, url: str, **_k) -> bytes:
        self.downloads.append(url)
        return super()._download(url)

    def _produce_template_video(self, *, workdir, specs, words, narration_seconds, audio_file, fonts_dir,
                                logo_path, threads, heartbeat, run_id, max_seconds, layout_engine,
                                hook_card=False, push_in=False):
        from marketing import voice as vc

        self.renders += 1
        self.video_calls.append({"hook_card": hook_card, "push_in": push_in, "kinds": [s.kind for s in specs]})
        assert (Path(workdir) / audio_file).read_bytes().startswith(b"e2e-m4a:")
        return b"e2e-mp4:" + str(len(specs)).encode(), narration_seconds + vc.DISCLAIMER_CARD_SECONDS, \
            list(range(len(specs)))


def test_a_template_day_goes_from_the_build_to_posts_held_for_review_through_the_real_app(monkeypatch):
    pytest.importorskip("PIL")
    from app.services.marketing import news_templates
    from app.services.marketing import template_onscreen as tos

    world = TemplateWorld(monkeypatch)
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready"
    # the script: the template build's ONE accepted row — no model, no tokens
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    out = script["output"]
    assert (script["status"], script["template_id"], script["tokens_used"], script["model"]) == (
        "accepted", "ceo_buys", 0, None)
    assert (out["authorship"], out["content_class"], out["video_layout"]) == ("template", "C", "per_line")
    assert world.news.calls == ["ceo_buys"]
    # the run mirror: the script's class and series
    assert (run["content_class"], run["template_id"], run["metadata"]["series"]) == ("C", "ceo_buys", "ceo_buys")
    # logos: fetched once each, stored content-addressed, then downloaded by the worker by their sha
    assert world.news.logo_calls == ["LOW", "SBUX", "NKE"]         # owner decisions 2026-11-20: three at least
    assert sorted(world.logo_uploads) == sorted(lg["url"].split("/marketing-media/", 1)[1] for lg in out["logos"])
    assert all(lg["url"] and lg["sha256"] for lg in out["logos"])
    assert sorted(u for u in world.downloads if "/logos/" in u) == sorted(lg["url"] for lg in out["logos"])
    # the video: the opening card over the hook, one card per line, the disclaimer last — and no Batch 2
    # feature (MARKETING_VIDEO_FEATURES is off: nothing frozen, no push-in, no data card)
    (video_call,) = world.video_calls
    assert video_call["hook_card"] is True and video_call["kinds"] == ["opening", "text", "text", "text",
                                                                      "text", "disclaimer"]
    assert video_call["push_in"] is False and "video_features" not in out
    (video,) = _ready(world, "video")
    allowed_video = (set(tos.opening_strings(out["opening_card"], out["logos"]))
                     | {c[k] for c in out["cards"] for k in ("title", "body")} | {out["disclaimer_card"]}
                     | {out["image_footer"]})                # news-v23: each card's short footer line
    drawn_video = set(video["metadata"]["onscreen_text"])
    assert out["disclaimer_card"] in drawn_video and out["opening_card"]["headline"] in drawn_video
    assert drawn_video - allowed_video <= set(mrs.VIDEO_BRAND_TEXT)
    # the post image: its spec's strings and the footer — never the alt text
    card = _the_card(world)
    drawn_image = set(card["metadata"]["onscreen_text"])
    assert out["image_footer"] in drawn_image
    allowed_image = set(tos.image_strings(out["image_spec"], out["logos"])) | {out["image_footer"]}
    assert drawn_image <= allowed_image | {"Caydex"}          # news-v23: + the brand row's word (PICTURE_BRAND_TEXT)
    alt_only = {out["image_post"]["title"], *out["image_post"]["paragraphs"]} - allowed_image
    assert alt_only and not drawn_image & alt_only          # the alt text's own sentences are never drawn
    # posts: each platform in its frozen format, every one held for review, carrying what it is
    posts = {(p["platform"], p["format"]): p for p in world.posts()}
    assert {(p, f) for (p, f) in posts} == set(out["post_formats"].items())
    assert all(p["status"] == "pending_review" for p in posts.values())
    for (platform, fmt), p in posts.items():
        md = p["metadata"]
        assert (md["content_class"], md["series"], md["authorship"]) == ("C", "ceo_buys", "template")
        assert md["made_with_ai"] is (fmt == "video")
        assert p["caption"] == out["posts"][platform]["caption"]
        if fmt == "image":
            assert p["asset_ids"] == [card["id"]]
        if fmt == "video":
            assert p["asset_ids"] == [video["id"]]
    # what was recorded still re-checks clean against the stored fact sheet
    assert news_templates.revalidate(out, fact_sheet=script["fact_sheet"],
                                     run_date=mrs.run_date_et(datetime.now(timezone.utc))) == []


def test_a_template_day_switched_off_before_its_posts_is_skipped_template_refused(monkeypatch):
    """Rollback (contract D17): the class switch goes back to "A" after the template was accepted and
    rendered — create_posts refuses it 409 MARKETING_TEMPLATE_REFUSED and the real worker closes the day
    `skipped` (template_refused) after ONE call, recording nothing."""
    pytest.importorskip("PIL")
    world = TemplateWorld(monkeypatch)
    posts_calls: List[str] = []

    def switch_off_before_posts(request):
        if request.method == "POST" and request.url.path.endswith("/posts"):
            posts_calls.append(request.url.path)
            monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A")
        return None

    world.before = switch_off_before_posts
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "skipped" and run["metadata"]["skip_reason"] == "template_refused"
    assert world.posts() == [] and len(posts_calls) == 1


#: What a news-v21 Company Weekly row stored as its picture footer (the long one the restyle replaced).
_NEWS_V21_FOOTER = ("Educational only · not investment advice · Source: SEC Form 4 filings · Filed Nov 9–15, 2026 · "
                    "Caydex · Not affiliated with anyone named")


def test_final_a_template_accepted_under_another_version_is_skipped_at_its_first_media_registration(monkeypatch):
    """Final review 2026-11-20 — the deploy window: a Company Weekly day accepted under news-v21 (today's web) and
    resumed after the news-v23 deploy, before `assets_ready`. Its picture drew the stored LONG footer, which the
    on-screen check refused 422 — `_record_failure`, the run `failed`, re-claimed and re-rendered up to six times with
    failed-run alerts — for a day create_posts' `template_changed` would skip anyway. Here the real worker meets the
    real app with the stored row turned into that news-v21 row before its first media registration: the server
    answers 409 MARKETING_TEMPLATE_REFUSED and the worker closes the day `skipped` (template_refused) after ONE
    registration, nothing registered, no post. Mutation-checked by hand (2026-11-20): with the server's version
    check removed the run ended `failed` (the 422), and with the worker's branch removed it ended `failed` too."""
    pytest.importorskip("PIL")
    world = TemplateWorld(monkeypatch)
    media: List[str] = []

    def the_row_was_accepted_by_news_v21(request):
        if request.method == "POST" and request.url.path.endswith("/assets"):
            kind = json.loads(request.content or b"{}").get("kind")
            if kind in ("card", "video"):
                media.append(kind)
                (script,) = world.fake.tables[mrs.SCRIPTS].rows
                script["output"]["template_version"] = "news-v21"
                script["output"]["image_footer"] = _NEWS_V21_FOOTER
        return None

    world.before = the_row_was_accepted_by_news_v21
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "skipped" and run["metadata"]["skip_reason"] == "template_refused", run
    assert media == ["card"]                                  # one registration: deterministic, never retried
    assert not world.assets("card") and not world.assets("video") and world.posts() == []


# ══ Drop 2b: a 2b day — the real worker against the real app, on the real calendar ══════════════════
#
# Shipped in code, the four 2b series run only once MARKETING_NEWS_SERIES lists them (default: off). Each
# case pins the ET date to a day whose REAL calendar chain reaches the series, lists every series, and
# lets a stub adapter answer that one series (the steps before it come up empty). Everything else is
# real: the build, the templates and their re-check, the logos stored and fetched by sha, the per-line
# video with its opening card, the post image in its own layout (`spotlight`, the newly shipped `pair` and
# `grid`, `rows`), the server's checks of what each declares it drew, and create_posts' re-check.

_EVERY_SERIES = "ceo_buys,insider_buys,thirteen_f,congress_count,company_stakes,earnings,money_map,theme_explainer"


class _StubNews2b:
    """The adapter's public surface: `series` answers with `record`; every other series comes up empty."""

    MarketingNewsUnavailable = _StubNews.MarketingNewsUnavailable

    def __init__(self, series: str, record: Any) -> None:
        self.series, self.record = series, record
        self.calls: List[str] = []
        self.logo_calls: List[str] = []

    async def candidates(self, series, *, run_date, exclude, limit, deadline, **_kw):
        from types import SimpleNamespace

        self.calls.append(series)
        if series == self.series:
            return SimpleNamespace(series=series, records=(self.record,), skip_reason=None, rejections={})
        return SimpleNamespace(series=series, records=(), skip_reason=f"{series}_none_qualified", rejections={})

    async def fetch_logo(self, symbol, *, max_bytes, timeout):
        self.logo_calls.append(symbol)
        return _logo_png(symbol), "image/png"


def _2b_day(series: str):
    """(record, run date, class, image layout, the series the build asks, in order) — fictional companies."""
    from datetime import date

    from app.services.marketing import company_news_rules as R

    co = R.CompanyRef
    if series == "congress_count":      # the December Congress Count Tuesday (in the 13F season)
        day = date(2026, 12, 8)
        # news-v23 (the restyle, 2026-11-19): ONE list of the month's 2-3 companies
        counts = tuple(R.CongressCount(series=series, company=co(symbol=sym, name=name), month="2026-11",
                                       members=members, fetched_on=day)
                       for sym, name, members in (("CTSO", "Contoso", 4), ("FBKM", "Fabrikam", 2)))
        return (R.CongressMonth(series=series, month="2026-11", fetched_on=day, counts=counts), day, "C", "rows",
                ["congress_count"])
    if series == "company_stakes":      # the first stakes Tuesday, after the 13F season
        day = date(2026, 12, 29)
        # news-v23 (owner 2026-11-19, "public only" + "add 1-3 more companies"): ONE investor's stakes in public
        # companies — a `rows` list, its cover the list
        investor = co(symbol="FBKM", name="Fabrikam")
        rows = (R.CompanyStake(series=series, stake_id="0a1b2c3d-4e5f-4a6b-8c7d-0000000e2e01", investor=investor,
                               investee_name="Relecloud", investee=co(symbol="RLCD", name="Relecloud"),
                               kind="us_listed_off_13f", value_usd=640_000_000.0, value_basis="invested",
                               ownership_pct=None, as_of=date(2026, 10, 15), verified_on=date(2026, 12, 15),
                               source_title="Relecloud 8-K", background=None, listed_since=None,
                               local_listing=None, is_new=True),
                R.CompanyStake(series=series, stake_id="0a1b2c3d-4e5f-4a6b-8c7d-0000000e2e02", investor=investor,
                               investee_name="Proseware", investee=co(symbol="PRSW", name="Proseware"),
                               kind="on_13f_note", value_usd=310_000_000.0, value_basis="fair_value",
                               ownership_pct=None, as_of=date(2026, 9, 30), verified_on=date(2026, 12, 15),
                               source_title="Fabrikam 10-Q (quarter ended Sep 30, 2026)", background=None,
                               listed_since=None, local_listing=None, is_new=True))
        return (R.CompanyStakes(series=series, investor=investor, stakes=rows), day, "F", "rows", ["company_stakes"])
    if series == "earnings":            # the first 2b-3 Thursday (the January season)
        day = date(2027, 1, 14)
        # news-v23 (the restyle, 2026-11-19): ONE table of 2-4 companies
        reports = (R.EarningsReport(series=series, company=co(symbol="TSPN", name="Tailspin Toys"),
                                    report_date=date(2027, 1, 11), period_end=date(2026, 12, 31), eps_actual=1.42,
                                    eps_estimate=1.3, revenue_actual=1_284_600_000.0,
                                    revenue_estimate=1_251_300_000.0),
                   R.EarningsReport(series=series, company=co(symbol="NWTR", name="Northwind Traders"),
                                    report_date=date(2027, 1, 12), period_end=date(2026, 12, 31), eps_actual=-0.05,
                                    eps_estimate=-0.12, revenue_actual=551_900_000.0,
                                    revenue_estimate=543_600_000.0))
        return (R.EarningsDay(series=series, reports=reports), day, "F", "rows", ["earnings"])
    # theme_explainer (news-v23 phase 3): a Wednesday out of the earnings season — the theme leads its chain
    day = date(2026, 12, 2)
    rows = (("CTSO", "Contoso", "Robotics systems", 0.62), ("FBKM", "Fabrikam", "Automation software", 0.48),
            ("NWTR", "Northwind Traders", "Warehouse systems", 0.71), ("TSPN", "Tailspin Toys", "Drones", 0.39),
            ("ADVW", "Adventure Works", "Field services", 0.55), ("PRSW", "Proseware", "Sensors", None),
            ("WDGB", "Woodgrove Bank", None, None), ("LTWR", "Litware", "Logistics software", 0.66))
    members = tuple(R.ThemeMember(company=co(symbol=sym, name=name), top_segment=seg, top_segment_share=share,
                                  fiscal_year="2025" if seg else None) for sym, name, seg, share in rows)
    return (R.ThemeExplainer(series=series, slug="warehouse-robots", title="Warehouse robots", members=members,
                             tickers_as_of=date(2026, 11, 16), angle="make_money", posted_on=day),
            day, "F", "grid", ["theme_explainer"])


def _theme_calendar(monkeypatch, series: str) -> None:
    """news-v23 phase 3: the weekly theme post runs on Wednesday only — a posting day of the owner's production
    calendar (documents/OWNER_TASKS.md), which the declared default (mon,tue,thu,sat) rests."""
    if series == "theme_explainer":
        from app.services.marketing import selection

        monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,wed,thu,sat")


def test_phase3_the_market_value_angle_goes_from_the_build_to_posts_through_the_real_app(monkeypatch):
    """news-v23 phase 3, angle 2 ("who's biggest", theme_biggest_power.jpg) end to end: a Wednesday, the REAL worker
    (it declares theme_angles) is handed the `ranked` picture and the list cover, draws them, and the REAL server
    accepts what each declares — every drawn string the stored spec's, the footer naming its market-data source — and
    the day's posts are held for review. Fictional companies, invented values."""
    pytest.importorskip("PIL")
    from datetime import date

    from app.services.marketing import company_news_rules as R
    from app.services.marketing import selection
    from app.services.marketing import template_onscreen as tos

    co = R.CompanyRef
    day = date(2026, 12, 2)
    rows = (("CTSO", "Contoso", 182e9), ("FBKM", "Fabrikam", 96.4e9), ("NWTR", "Northwind Traders", 41e9),
            ("TSPN", "Tailspin Toys", 12.7e9), ("ADVW", "Adventure Works", 3.9e9), ("LTWR", "Litware", 820e6))
    record = R.ThemeExplainer(series="theme_explainer", slug="grid-builders", title="Grid builders",
                              members=tuple(R.ThemeMember(company=co(symbol=s_, name=n), top_segment=None,
                                                          top_segment_share=None, fiscal_year=None,
                                                          market_value_usd=v) for s_, n, v in rows),
                              tickers_as_of=date(2026, 11, 20), theme_size=7, angle="biggest", posted_on=day,
                              values_as_of=date(2026, 12, 1))
    _theme_calendar(monkeypatch, "theme_explainer")
    assert selection.plan_for(day).chain[0] == "theme_explainer"
    world = TemplateWorld(monkeypatch, news=_StubNews2b("theme_explainer", record), run_date=day,
                          news_series=_EVERY_SERIES)
    _theme_calendar(monkeypatch, "theme_explainer")
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready"
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    out = script["output"]
    assert script["source_ref"] == "news:theme_explainer:grid-builders:biggest:2026-12-02"
    assert out["image_spec"]["layout"] == "ranked" and len(out["image_spec"]["rows"]) == 6
    assert out["image_footer"] == "Not investment advice · Source: company filings and market data"
    card = _the_card(world)
    drawn_image = set(card["metadata"]["onscreen_text"])
    assert drawn_image <= set(tos.image_strings(out["image_spec"], out["logos"])) | {out["image_footer"], "Caydex"}
    assert {"$182B", "Contoso", "1", out["image_footer"]} <= drawn_image
    (video,) = _ready(world, "video")
    drawn_video = set(video["metadata"]["onscreen_text"])
    assert out["opening_card"]["list"]["rows"][0]["cells"] == ["Contoso", "$182B"]
    assert set(tos.opening_strings(out["opening_card"], out["logos"])) & drawn_video >= {"Contoso", "$182B"}
    posts = world.posts()
    assert posts and all(p["status"] == "pending_review" for p in posts)
    assert {f for p in posts for f in [p["format"]]} >= {"image", "video"}


def _phase3_table_record(angle: str, day):
    """A table angle's record of fictional companies with invented figures (2026-11-20, angles 3-5)."""
    from datetime import timedelta

    from app.services.marketing import company_news_rules as R

    cos = [R.CompanyRef(symbol=s_, name=n) for s_, n in (("CTSO", "Contoso"), ("FBKM", "Fabrikam"),
                                                        ("NWTR", "Northwind Traders"), ("TSPN", "Tailspin Toys"),
                                                        ("ADVW", "Adventure Works"))]
    base = dict(series="theme_explainer", slug="grid-builders", title="Grid builders", tickers_as_of=day - timedelta(
        days=12), theme_size=7, angle=angle, posted_on=day)
    if angle == "growth_profit":
        figs = ((4.2e9, 3.1e9, 0.61e9), (1.9e9, 1.8e9, -0.24e9), (9.6e9, 9.9e9, 1.4e9), (0.82e9, 0.55e9, 0.04e9),
                (2.7e9, 2.2e9, 0.45e9))
        members = tuple(R.ThemeMember(company=c, top_segment=None, top_segment_share=None, fiscal_year="2025",
                                      revenue_usd=r, prior_revenue_usd=p, net_income_usd=n)
                        for c, (r, p, n) in zip(cos, figs))
        return R.ThemeExplainer(members=members, **base)
    if angle == "insiders":
        figs = ((1.3e6, 6.4e6), (0.42e6, 0.0), (0.0, 41.2e6), (0.0, 12.9e6), (0.0, 0.8e6))
        members = tuple(R.ThemeMember(company=c, top_segment=None, top_segment_share=None, fiscal_year=None,
                                      purchases_usd=b, sales_usd=sl) for c, (b, sl) in zip(cos, figs))
        return R.ThemeExplainer(members=tuple(sorted(members, key=R.theme_insider_order_key)),
                                window_start=day - timedelta(days=90), window_end=day - timedelta(days=1),
                                companies_checked=6, **base)
    members = tuple(R.ThemeMember(company=c, top_segment=None, top_segment_share=None, fiscal_year=None,
                                  congress_members=n) for c, n in zip(cos, (5, 3, 2, 2, 2)))
    return R.ThemeExplainer(members=tuple(sorted(members, key=R.theme_congress_order_key)), month="2026-11", **base)


@pytest.mark.parametrize("angle, layout", [("growth_profit", "figures"), ("insiders", "ledger"), ("congress", "tally")])
def test_phase3_a_table_angle_goes_from_the_build_to_posts_through_the_real_app(monkeypatch, angle, layout):
    """2026-11-20, angles 3-5 end to end: a Wednesday, the REAL worker (it declares theme_angles) is handed the table
    picture and its six-row table cover, draws them, and the REAL server accepts what each declares — every drawn
    string the stored spec's (the cover's the stored list's), the footer its family's — and the day's posts are held
    for review. Fictional companies, invented figures."""
    pytest.importorskip("PIL")
    from datetime import date

    from app.services.marketing import selection
    from app.services.marketing import template_onscreen as tos

    day = date(2026, 12, 9)
    record = _phase3_table_record(angle, day)
    _theme_calendar(monkeypatch, "theme_explainer")
    assert selection.plan_for(day).chain[0] == "theme_explainer"
    world = TemplateWorld(monkeypatch, news=_StubNews2b("theme_explainer", record), run_date=day,
                          news_series=_EVERY_SERIES)
    _theme_calendar(monkeypatch, "theme_explainer")
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready"
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    out = script["output"]
    assert script["source_ref"] == f"news:theme_explainer:grid-builders:{angle}:2026-12-09"
    assert out["image_spec"]["layout"] == layout and out["opening_card"]["list"]["layout"] == layout
    card = _the_card(world)
    drawn_image = set(card["metadata"]["onscreen_text"])
    assert drawn_image <= set(tos.image_strings(out["image_spec"], out["logos"])) | {out["image_footer"], "Caydex"}
    assert {"Contoso", out["image_footer"], out["image_spec"]["note"]} <= drawn_image
    (video,) = _ready(world, "video")
    drawn_video = set(video["metadata"]["onscreen_text"])
    assert set(tos.opening_strings(out["opening_card"], out["logos"])) & drawn_video >= {
        "Contoso", out["opening_card"]["list"]["heading"]}
    posts = world.posts()
    assert posts and all(p["status"] == "pending_review" for p in posts)
    assert {p["format"] for p in posts} >= {"image", "video"}


@pytest.mark.parametrize("series", ["congress_count", "company_stakes", "earnings", "theme_explainer"])
def test_a_2b_day_goes_from_the_build_to_posts_held_for_review_through_the_real_app(monkeypatch, series):
    pytest.importorskip("PIL")
    from app.services.marketing import news_templates, selection
    from app.services.marketing import template_onscreen as tos

    record, day, klass, layout, asked = _2b_day(series)
    _theme_calendar(monkeypatch, series)
    assert series in selection.plan_for(day).chain                       # the real calendar reaches it
    world = TemplateWorld(monkeypatch, news=_StubNews2b(series, record), run_date=day, news_series=_EVERY_SERIES)
    _theme_calendar(monkeypatch, series)                                 # the world pins the default calendar
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["run_date"] == day.isoformat()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready"
    # the script: ONE accepted template row — no model, no tokens — reached through the day's chain
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    out = script["output"]
    assert (script["status"], script["template_id"], script["tokens_used"], script["model"]) == (
        "accepted", series, 0, None)
    assert (out["authorship"], out["content_class"], out["series"], out["video_layout"]) == (
        "template", klass, series, "per_line")
    assert world.news.calls == asked
    assert [t["series"] for t in script["fact_sheet"]["selection"]["trail"]] == asked
    assert (run["content_class"], run["template_id"], run["metadata"]["series"]) == (klass, series, series)
    # logos: fetched once each, stored content-addressed, downloaded by the worker by their sha
    assert sorted(world.logo_uploads) == sorted(lg["url"].split("/marketing-media/", 1)[1] for lg in out["logos"])
    assert out["logos"] and all(lg["url"] and lg["sha256"] for lg in out["logos"])
    assert sorted(u for u in world.downloads if "/logos/" in u) == sorted(lg["url"] for lg in out["logos"])
    # the video: the opening card over the hook, one card per line, the disclaimer last
    (video_call,) = world.video_calls
    assert video_call["hook_card"] is True
    assert video_call["kinds"] == ["opening", "text", "text", "text", "text", "disclaimer"]
    (video,) = _ready(world, "video")
    allowed_video = (set(tos.opening_strings(out["opening_card"], out["logos"]))
                     | {c[k] for c in out["cards"] for k in ("title", "body")} | {out["disclaimer_card"]}
                     | {out["image_footer"]})                # news-v23: each card's short footer line
    drawn_video = set(video["metadata"]["onscreen_text"])
    assert out["disclaimer_card"] in drawn_video and out["opening_card"]["headline"] in drawn_video
    assert drawn_video - allowed_video <= set(mrs.VIDEO_BRAND_TEXT)
    # the post image: drawn in the series' own layout, its spec's strings and the footer only
    assert out["image_spec"]["layout"] == layout
    card = _the_card(world)
    drawn_image = set(card["metadata"]["onscreen_text"])
    assert out["image_footer"] in drawn_image
    assert drawn_image <= (set(tos.image_strings(out["image_spec"], out["logos"])) | {out["image_footer"]}
                           | {"Caydex"})                     # news-v23: + the brand row's word (PICTURE_BRAND_TEXT)
    assert not drawn_image & ({out["image_post"]["title"], *out["image_post"]["paragraphs"]}
                              - set(tos.image_strings(out["image_spec"], out["logos"])))
    # posts: each platform in its frozen format, every one held for review, carrying what it is
    posts = {(p["platform"], p["format"]): p for p in world.posts()}
    assert set(posts) == set(out["post_formats"].items())
    assert {"image", "video"} <= {f for _p, f in posts}
    assert all(p["status"] == "pending_review" for p in posts.values())
    for (platform, fmt), p in posts.items():
        md = p["metadata"]
        assert (md["content_class"], md["series"], md["authorship"]) == (klass, series, "template")
        assert md["made_with_ai"] is (fmt == "video")
        assert p["caption"] == out["posts"][platform]["caption"]
        assert p["asset_ids"] == ([card["id"]] if fmt == "image" else [video["id"]] if fmt == "video" else [])
    if series == "congress_count":
        # a count of members, "disclosed", never a dollar figure or a member — on every surface
        # news-v23: ONE list of the month's companies — each row's count cell, most members first
        assert [r["cells"][-1] for r in out["image_spec"]["sections"][0]["rows"]] == ["4", "2"]
        assert "disclosed" in out["hook"]
        texts = [p["caption"] for p in posts.values()] + sorted(drawn_video | drawn_image)
        assert not [t for t in texts if "$" in t]
    assert news_templates.revalidate(out, fact_sheet=script["fact_sheet"], run_date=day) == []


def test_a_2b_day_whose_series_is_switched_off_before_its_posts_is_skipped_template_refused(monkeypatch):
    """The per-series switch is read again at create_posts: the owner takes congress_count back out of
    MARKETING_NEWS_SERIES after its day was built and rendered — the real worker gets 409
    MARKETING_TEMPLATE_REFUSED and closes the day `skipped` (template_refused) after ONE call, recording
    nothing."""
    pytest.importorskip("PIL")
    record, day, _klass, _layout, _asked = _2b_day("congress_count")
    world = TemplateWorld(monkeypatch, news=_StubNews2b("congress_count", record), run_date=day,
                          news_series=_EVERY_SERIES)
    posts_calls: List[str] = []

    def switch_off_before_posts(request):
        if request.method == "POST" and request.url.path.endswith("/posts"):
            posts_calls.append(request.url.path)
            monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "ceo_buys,insider_buys,thirteen_f,money_map")
        return None

    world.before = switch_off_before_posts
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "skipped" and run["metadata"]["skip_reason"] == "template_refused"
    assert world.posts() == [] and len(posts_calls) == 1
    assert _ready(world, "video") and _the_card(world)             # it was built and rendered first


# ══ Today's production lesson day (post-commit check, 2026-10-10) ═══════════════════════════════════
# What production runs since eba8ddcc: MARKETING_CONTENT_CLASSES unset (lessons only), MARKETING_IMAGE_POSTS
# and MARKETING_X_IMAGES off, both dry-run switches off. Every accepted lesson now goes through
# `_frozen_output` → `freeze_post_formats(image_posts=False)`, so the stored output carries `post_formats`
# for all eight outlets — and still the writer's `image_post` (validated and stored either way), which
# nothing may draw. The tests above seed a pre-drop-1 package (no formats, two outlets, a dry run) or an
# image day; this one drives the shape the daily run writes today, through the real worker and app, and
# then has each outlet's REAL publisher adapter build its request from the rows the worker left.

_EVERY_OUTLET = ("bluesky", "x", "youtube", "facebook", "linkedin", "threads", "instagram", "tiktok")


def test_todays_lesson_day_records_all_eight_outlets_in_their_frozen_formats_through_the_real_app(monkeypatch):
    from app.services.marketing import outlet_upload_post as oup
    from app.services.marketing import outlets
    from marketing import cards

    for name in ("MARKETING_IMAGE_POSTS", "MARKETING_X_IMAGES", "MARKETING_DRY_RUN"):
        monkeypatch.setattr(settings, name, False)
    monkeypatch.setattr(settings, "MARKETING_CONTENT_CLASSES", "A")     # its default: lessons only (unset)
    copy = {p: {"platform": p, "title": "Your moody business partner" if p == "youtube" else None,
                "caption": f"server {p} copy"} for p in _EVERY_OUTLET}
    run_date = mrs.run_date_et(datetime.now(timezone.utc))
    frozen = ss.freeze_post_formats({**_PACKAGE, "posts": copy, "image_post": dict(_IMAGE_POST)}, run_date,
                                    image_posts=False, x_images=False)
    assert frozen["post_formats"] == {"tiktok": "video", "youtube": "video", "instagram": "video",
                                      "x": "text", "bluesky": "text", "threads": "text",
                                      "facebook": "text", "linkedin": "text"}
    assert frozen["image_post"] == _IMAGE_POST and "image_footer" not in frozen
    world = World(monkeypatch, frozen)
    monkeypatch.setenv("MARKETING_DRY_RUN", "false")                    # the worker reads it in main()

    def never_drawn(*_a, **_k):
        raise AssertionError("image posts are off: the post image is never drawn")

    monkeypatch.setattr(cards, "image_for_script", never_drawn)
    monkeypatch.setattr(cards, "render_image", never_drawn)
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready" and run["dry_run"] is False
    (video,) = _ready(world, "video")
    assert run["metadata"]["video_asset_id"] == video["id"] and not run["metadata"].get("image_asset_id")
    # No post image: none drawn, registered or uploaded — one video for the three video outlets.
    assert world.assets("card") == [] and not [c for c in world.calls if "/card-" in c[2]]
    assert world.renders == 1
    # Posts: exactly one per frozen (platform, format), each held for review, not a rehearsal.
    assert len(world.posts()) == len(_EVERY_OUTLET)
    posts = {(p["platform"], p["format"]): p for p in world.posts()}
    assert set(posts) == set(frozen["post_formats"].items())
    fields: Dict[str, Dict[str, Any]] = {}
    for (platform, fmt), p in posts.items():
        md = p["metadata"]
        assert p["status"] == "pending_review" and md["dry_run"] is False
        assert (md["content_class"], md["made_with_ai"]) == ("A", True)
        assert p["caption"] == copy[platform]["caption"]
        assert p["asset_ids"] == ([video["id"]] if fmt == "video" else [])
        # What the publisher would send: the outlet's REAL adapter builds the request from this row.
        prepared = outlets.adapter_for(platform).prepare(p)
        if platform in oup.ADAPTERS:
            assert prepared.payload["kind"] == ("video" if fmt == "video" else "text")
            assert prepared.payload["asset_id"] == (video["id"] if fmt == "video" else None)
            fields[platform] = prepared.payload["fields"]
    assert sorted(fields) == ["facebook", "instagram", "linkedin", "threads", "tiktok", "youtube"]
    # The cover is pinned to the opening card where Upload-Post can: TikTok and Instagram; no other
    # outlet's request carries a pin (YouTube Shorts pick their own frame; text posts have none).
    assert fields["tiktok"]["cover_timestamp"] == oup.VIDEO_COVER_MS
    assert fields["instagram"]["thumb_offset"] == str(oup.VIDEO_COVER_MS)
    pins = {"cover_timestamp", "thumb_offset"}
    assert [p for p in ("youtube", "facebook", "linkedin", "threads") if pins & set(fields[p])] == []


# ══ Batch 2 ("first seconds", 2026-10-19): the frozen video features, through the real worker and app ════
#
# MARKETING_VIDEO_FEATURES lists every feature. The REAL write path freezes the KIND's own into the accepted
# output — a lesson's hook card + push-in (`_frozen_output`, run on the seeded package: `freeze_on_seed`), a
# template's data card + push-in (the real template build) — only because the REAL worker's claim declared
# `video_v2`; the real read-back carries them beside the end card's CTA; the worker's REAL
# `render.produce_video` draws the feature cards (cards → PNGs, captions.ass, the timeline — only its ONE
# ffmpeg call is faked, `_FfmpegFake`) and declares every string; the real server allows the hook / the
# picture only because the feature is frozen, and records the posts held for review. With the switch off
# (its default) a day is today's video, keyword for keyword.

_ALL_FEATURES = "hook_card,data_card,push_in"
_STORE_CTA = "Search Caydex on the App Store"
_LEGACY_CTA = "caydexinvest.com"
#: news-v23 (restyle 2026-11-19): every LESSON video card also draws its kicker pill, the short footer line and the
#: brand row's word — the server's LESSON_BRAND_TEXT + post_copy.IMAGE_FOOTER_HEAD. render.py declares each string
#: once, in card order, so they follow the FIRST card's own strings.
_LESSON_CHROME = ["INVESTING LESSON", "Not investment advice", "Caydex"]
#: A valid App Store URL (the app's public listing): the store state at write time is then LIVE.
_STORE_URL = "https://apps.apple.com/us/app/caydex/id6759525689"

#: A production-shaped lesson — the hook, six lines, three cards — accepted while the app is on the store
#: (its store state is frozen with its captions: the end card then names the App Store, owner decision 6).
_LESSON = {
    **_PACKAGE,
    "video_script": [
        "Every day, he offers a price for your share of the business.",
        "Some days he is gloomy, and some days he is giddy.",
        "His price follows his mood, not the business itself.",
        "You are never forced to accept his offer.",
        "The business keeps doing its work, whatever he says today.",
        "Knowing that is what keeps you calm when prices swing.",
    ],
    "cards": [{"title": "A partner with moods", "body": "Every day he names a price for your share."},
              {"title": "Mood, not value", "body": "His price follows his mood, not the business itself."},
              {"title": "Your choice", "body": "You never have to accept his offer on a gloomy day."}],
    "store_state": "live",
}
#: Seven narrated lines of the fake narration (1 s each, LINE_PAUSE_SECONDS between): ~8.7 s of words.
_LESSON_AUDIO_SECONDS = 9.0


class _FfmpegFake:
    """`video.render_video` — the ONE ffmpeg call of the REAL `render.produce_video` — faked: it checks what
    the real render prepared in the job directory (every card a 1080×1920 PNG, the captions file, the
    narration) and records it with the segments and `push_in`; it answers a probe exactly as long as the
    timeline, as the ffprobe gate requires."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, *, workdir, card_files, segments, audio_file, ass_file, fonts_dir, threads, heartbeat,
                 run_id, max_seconds, max_bytes, push_in=False):
        from PIL import Image

        from marketing import video as vd

        work = Path(workdir)
        for name in sorted(set(card_files)):
            with Image.open(work / name) as im:
                assert (im.format, im.size) == ("PNG", vd.SIZE), name
        assert (work / audio_file).read_bytes().startswith(b"e2e-m4a:")
        assert any(f.suffix == ".ttf" for f in (work / fonts_dir).iterdir())
        self.calls.append({"card_files": list(card_files), "segments": list(segments), "push_in": push_in,
                           "ass": (work / ass_file).read_text(encoding="utf-8")})
        return b"e2e-mp4:real-cards", vd.ProbeResult(duration=segments[-1].end)


def _real_render(world, monkeypatch) -> _FfmpegFake:
    """The REAL produce_video for this world, with only its ffmpeg call faked."""
    from marketing import render as rd
    from marketing import video as vd

    ffmpeg = _FfmpegFake()
    monkeypatch.setattr(rd, "produce_video", world.real_produce_video)
    monkeypatch.setattr(vd, "render_video", ffmpeg)
    return ffmpeg


def _readbacks(world) -> List[Dict[str, Any]]:
    """Every ACCEPTED script read-back the worker received (the kick's `script`), as it crossed the wire."""
    seen: List[Dict[str, Any]] = []

    def record(request, response):
        if request.method == "POST" and request.url.path.endswith("/script") and response.status_code == 200:
            body = json.loads(response.content)
            if body.get("status") == "accepted":
                seen.append(body["script"])
        return response

    world.after = record
    return seen


def _dialogue(ass: str) -> str:
    """The burned captions' text: every Dialogue event of a captions.ass, joined."""
    return "\n".join(line for line in ass.splitlines() if line.startswith("Dialogue:"))


def test_with_the_switch_off_a_lesson_day_is_todays_video_keyword_for_keyword(monkeypatch):
    """MARKETING_VIDEO_FEATURES off (its default) on a lesson accepted while the app is on the store: the
    write freezes nothing (no key), the read-back says so ([] beside the store's end card), and the worker
    calls produce_video with drop 1's keywords exactly — no hook card, no push-in — declaring exactly
    today's strings but the end card's CTA, which follows the store state (decision 6), never the switch."""
    monkeypatch.setattr(settings, "MARKETING_FIRST_COMMENT_PLATFORMS", "")
    world = World(monkeypatch, _LESSON, freeze_on_seed=True)
    readbacks = _readbacks(world)
    assert world.worker.main() == 0
    assert world._run_row()["status"] == "media_ready"
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    assert "video_features" not in script["output"]
    assert readbacks and all(r["video_features"] == [] and r["end_card_cta"] == _STORE_CTA for r in readbacks)
    assert world.openings == [{}]
    (video,) = _ready(world, "video")
    md = video["metadata"]
    assert md["video_features"] == [] and md["card_kinds"] == ["text", "text", "text", "disclaimer"]
    first, *rest = _LESSON["cards"]
    assert md["onscreen_text"] == [first["title"], first["body"], *_LESSON_CHROME,
                                   *[c[k] for c in rest for k in ("title", "body")],
                                   _LESSON["disclaimer_card"], _STORE_CTA]


def test_the_default_world_keeps_drop_1s_video_and_its_legacy_end_card(world):
    """The pre-Batch-2 shape end to end (a package with no store state, never frozen): [] and the LEGACY end
    card on the wire, drop 1's call, drop 1's declared strings — byte for byte what the worker declared
    before Batch 2."""
    readbacks = _readbacks(world)
    assert world.worker.main() == 0
    assert readbacks and all(r["video_features"] == [] and r["end_card_cta"] == _LEGACY_CTA for r in readbacks)
    assert world.openings == [{}]
    (video,) = _ready(world, "video")
    assert video["metadata"]["onscreen_text"] == [
        "The partner", "He names a price every day.", *_LESSON_CHROME, "The lesson", "His mood is not the business.",
        _PACKAGE["disclaimer_card"], _LEGACY_CTA]


def test_a_lesson_day_with_every_feature_on_opens_on_its_hook_card_through_the_real_app(monkeypatch, caplog):
    pytest.importorskip("PIL")
    from app.services.marketing import outlet_upload_post as oup
    from marketing import video as vd

    monkeypatch.setattr(settings, "MARKETING_FIRST_COMMENT_PLATFORMS", "")
    world = World(monkeypatch, _LESSON, audio_seconds=_LESSON_AUDIO_SECONDS, freeze_on_seed=True)
    monkeypatch.setattr(settings, "MARKETING_VIDEO_FEATURES", _ALL_FEATURES)
    ffmpeg = _real_render(world, monkeypatch)
    readbacks = _readbacks(world)
    with caplog.at_level(logging.INFO):
        assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready"
    # The claim declared video_v2, so the write froze the LESSON's features (data_card is a template's).
    assert "video_v2" in run["metadata"]["worker_capabilities"]
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    assert script["output"]["video_features"] == ["hook_card", "push_in"]
    assert readbacks and all(r["video_features"] == ["hook_card", "push_in"] and r["end_card_cta"] == _STORE_CTA
                             for r in readbacks)
    # The real render: the hook card alone over the hook, then the three cards, the disclaimer last.
    (call,) = ffmpeg.calls
    segments = call["segments"]
    assert call["push_in"] is True and [s.card for s in segments] == [0, 1, 2, 3, 4]
    (audio,) = _ready(world, "audio")
    words = audio["metadata"]["words"]
    first_script_word = min(w["s"] for w in words if w["line"] == 1)
    assert abs(segments[0].end - first_script_word) <= 0.001         # the card leaves as line 1 starts
    # TikTok's / Instagram's cover frame (500 ms) is the hook card, before any crossfade.
    assert oup.VIDEO_COVER_MS / 1000 < segments[0].end
    # Its words are on the card, so the hook is not also captioned under it; line 1 on is.
    captions = _dialogue(call["ass"])
    assert "Meet" not in captions and "moody" not in captions and "Every" in captions and "swing." in captions
    # What the video declares: the hook VERBATIM first, the cards, the disclaimer, the store's end card —
    # all of it allowed by the real server only because hook_card is frozen.
    (video,) = _ready(world, "video")
    md = video["metadata"]
    assert md["video_features"] == ["hook_card", "push_in"]
    assert md["card_kinds"] == ["hook", "text", "text", "text", "disclaimer"]
    assert md["onscreen_text"] == [_LESSON["hook"], *_LESSON_CHROME,
                                   *[c[k] for c in _LESSON["cards"] for k in ("title", "body")],
                                   _LESSON["disclaimer_card"], _STORE_CTA]
    assert md["render_version"] == "render/v7" and md["video_version"] == vd.VIDEO_RENDER_VERSION
    # What was APPLIED beside what was frozen: card 0 pushes in (no fallback), and a lesson has no data card.
    assert md["push_in"] is True and "push_in_fallback" not in md and md["data_card_slot"] is None
    assert not [r for r in caplog.records if "App Store end card" in r.getMessage()]   # on the store: no warning
    # The posts: captions from the accepted package, the video post carrying the verified video, held — and
    # the run's frozen features noted on the VIDEO post only (B2-METRICS-ATTRIBUTION).
    posts = {(p["platform"], p["format"]): p for p in world.posts()}
    assert set(posts) == {("tiktok", "video"), ("x", "text")}
    assert posts[("tiktok", "video")]["asset_ids"] == [video["id"]]
    assert all(p["status"] == "pending_review" for p in posts.values())
    assert posts[("tiktok", "video")]["metadata"]["video_features"] == ["hook_card", "push_in"]
    assert "video_features" not in posts[("x", "text")]["metadata"]


def test_an_older_worker_keeps_todays_video_while_the_switch_is_on(monkeypatch, caplog):
    """The holder that did not declare video_v2 (a worker image before Batch 2) gets nothing frozen, whatever
    the switch says — the write says why (WARNING) — and so today's video, keyword for keyword."""
    world = World(monkeypatch, _LESSON, freeze_on_seed=True)
    monkeypatch.setattr(settings, "MARKETING_VIDEO_FEATURES", _ALL_FEATURES)
    monkeypatch.setattr(world.worker.BackendClient, "WORKER_CAPABILITIES",
                        ("post_image", "news_templates", "layouts_2b"))
    readbacks = _readbacks(world)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        assert world.worker.main() == 0
    assert world._run_row()["status"] == "media_ready"
    assert "video_v2" not in world._run_row()["metadata"]["worker_capabilities"]
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    assert "video_features" not in script["output"]
    assert readbacks and all(r["video_features"] == [] for r in readbacks)
    assert world.openings == [{}]
    assert len([r for r in caplog.records if "did not declare 'video_v2'" in r.getMessage()]) == 1


def test_a_read_back_without_the_batch_2_keys_draws_the_legacy_end_card_and_no_feature(monkeypatch):
    """An OLDER web's read-back carries neither `video_features` nor `end_card_cta` — here stripped on the
    wire from a run that froze every lesson feature and is on the store. The worker draws today's video
    with the LEGACY end card (the only CTA an older web allows), and the real server — which ALLOWS a
    frozen feature's strings but never requires them — accepts it."""
    world = World(monkeypatch, _LESSON, freeze_on_seed=True)
    monkeypatch.setattr(settings, "MARKETING_VIDEO_FEATURES", _ALL_FEATURES)
    stripped: List[Dict[str, Any]] = []

    def strip(request, response):
        if request.method == "POST" and request.url.path.endswith("/script") and response.status_code == 200:
            body = json.loads(response.content)
            if isinstance(body.get("script"), dict):
                for key in ("video_features", "end_card_cta"):
                    body["script"].pop(key, None)
                stripped.append(body["script"])
                return httpx.Response(response.status_code, json=body)
        return response

    world.after = strip
    assert world.worker.main() == 0
    assert world._run_row()["status"] == "media_ready"
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    assert script["output"]["video_features"] == ["hook_card", "push_in"]        # frozen on the server
    assert stripped and not any({"video_features", "end_card_cta"} & set(s) for s in stripped)
    assert world.openings == [{}]
    (video,) = _ready(world, "video")
    drawn = video["metadata"]["onscreen_text"]
    assert _LEGACY_CTA in drawn and _STORE_CTA not in drawn and _LESSON["hook"] not in drawn
    assert video["metadata"]["video_features"] == [] and video["metadata"]["card_kinds"][0] == "text"
    assert len(world.posts()) == 2


@pytest.mark.parametrize("kind", ["lesson", "template"])
def test_a_feature_card_the_run_did_not_freeze_is_refused_by_the_real_server(monkeypatch, kind):
    """The allow-list is the server's, never the worker's word: a worker that draws (and declares) a feature
    card the run did NOT freeze — the switch is off — is refused 422 by the real on-screen check, and
    nothing is posted. (A lesson's hook card; a template's data card — which a worker draws only at a slot
    the read-back names (CARDS-B2-1), and an unfrozen run's names none: the rogue worker forces one too.)"""
    from marketing import cards

    if kind == "lesson":
        world = World(monkeypatch, _LESSON, freeze_on_seed=True)
        forced = ("hook_card", "push_in")
    else:
        pytest.importorskip("PIL")
        # 2026-11-20: a roundup whose picture carries a note its list cover does not draw (`_noted_news`) — with the
        # default two direct rows the picture's every string is also the cover's, and the rogue card would be allowed
        world = TemplateWorld(monkeypatch, news=_noted_news())
        forced = ("data_card", "push_in")
        monkeypatch.setattr(cards, "data_card_slot", lambda script: 1)
    monkeypatch.setattr(cards, "video_features", lambda script: forced)
    refused: List[Dict[str, Any]] = []

    def record(request, response):
        if request.method == "POST" and request.url.path.endswith("/assets"):
            body = json.loads(request.content)
            if body.get("kind") == "video":
                refused.append({"status": response.status_code, "kinds": body["metadata"]["card_kinds"]})
        return response

    world.after = record
    assert world.worker.main() == 1
    run = world._run_row()
    assert run["status"] == "failed" and "MARKETING_REQUEST_INVALID" in (run["last_error"] or "")
    assert world.assets("video") == [] and world.posts() == []
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    assert "video_features" not in script["output"]
    # What the server refused really carried the forced card (never a vacuous pass on some other string).
    assert [r["status"] for r in refused] == [422]
    assert refused[0]["kinds"] == (["hook", "text", "text", "text", "disclaimer"] if kind == "lesson"
                                   else ["opening", "text", "data", "text", "text", "disclaimer"])


def _kept_cards(series: str, cards: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """The day's cards a data card NEVER replaces (`template_onscreen.DATA_CARD_KEEPS`: the series' hedge,
    caveat, explainer and source cards — on screen for their whole line on purpose, since news-v12)."""
    from app.services.marketing import template_onscreen as tos
    from app.services.marketing.news_templates import LEXICON

    kept = {LEXICON[i] for i in tos.DATA_CARD_KEEPS[series]}
    return [c for c in cards if c["title"] in kept]


def test_a_template_day_with_every_feature_on_draws_its_data_card_through_the_real_app(monkeypatch, caplog):
    pytest.importorskip("PIL")
    from app.services.marketing import news_templates
    from app.services.marketing import template_onscreen as tos

    monkeypatch.setattr(settings, "MARKETING_FIRST_COMMENT_PLATFORMS", "")
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", _STORE_URL)
    world = TemplateWorld(monkeypatch, audio_seconds=7.0)
    monkeypatch.setattr(settings, "MARKETING_VIDEO_FEATURES", _ALL_FEATURES)
    ffmpeg = _real_render(world, monkeypatch)
    readbacks = _readbacks(world)
    with caplog.at_level(logging.INFO):
        assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready"
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    out = script["output"]
    assert (out["authorship"], out["store_state"]) == ("template", "live")
    # The TEMPLATE's features (a hook card is a lesson's) — frozen by the real build's write.
    assert out["video_features"] == ["data_card", "push_in"]
    assert readbacks and all(r["video_features"] == ["data_card", "push_in"] and r["end_card_cta"] == _STORE_CTA
                             for r in readbacks)
    # CARDS-B2-1: the read-back names the card the data card replaces — the server's own choice from what it
    # stored (never stored itself), the FACT card "The purchase(s)" the picture shows, never the last card
    # (this week's last card is "Same week", a hedge the template keeps on screen on purpose).
    slot = readbacks[-1]["data_card_slot"]
    assert type(slot) is int and all(r["data_card_slot"] == slot for r in readbacks)
    assert "data_card_slot" not in out
    assert slot == tos.data_card_slot(out["series"], out["cards"], mrs.data_card_strings(out)) == 1
    assert out["cards"][slot]["title"] in (news_templates.LEXICON["ins.c2.title.one"],
                                           news_templates.LEXICON["ins.c2.title.many"])
    kept = _kept_cards("ceo_buys", out["cards"])
    assert out["cards"][-1] in kept and out["cards"][slot] not in kept
    # The real render: the opening card over the hook (still captioned: the card shows other words), the
    # line cards with the DATA card at the slot (specs[slot + 1]), the disclaimer; push-in on card 0.
    (call,) = ffmpeg.calls
    assert call["push_in"] is True
    assert out["hook"].split()[0] in _dialogue(call["ass"])
    (video,) = _ready(world, "video")
    md = video["metadata"]
    n_lines = len(out["video_script"])
    assert md["video_features"] == ["data_card", "push_in"] and md["push_in"] is True
    expected = ["opening", *["text"] * n_lines, "disclaimer"]
    expected[slot + 1] = "data"
    assert md["card_kinds"] == expected and md["data_card_slot"] == slot
    assert len(call["segments"]) == n_lines + 2
    # What it declares: the opening card, every line card but the slot's — the kept hedges among them — the
    # PICTURE's strings (its footer included, never the alt text) and the end card — each allowed only
    # because data_card is frozen.
    drawn = set(md["onscreen_text"])
    picture = set(tos.image_strings(out["image_spec"], out["logos"])) | {out["image_footer"]}
    allowed = (set(tos.opening_strings(out["opening_card"], out["logos"])) | picture
               | {c[k] for c in out["cards"] for k in ("title", "body")} | {out["disclaimer_card"], _STORE_CTA}
               | {"Caydex"})                                  # news-v23: the brand row's word
    assert drawn <= allowed and out["image_footer"] in drawn and _STORE_CTA in drawn
    assert out["opening_card"]["headline"] in drawn and out["opening_card"]["kicker"] in drawn
    assert not drawn & ({out["image_post"]["title"], *out["image_post"]["paragraphs"]} - picture)
    assert out["cards"][slot]["body"] not in drawn
    assert all({c["title"], c["body"]} <= drawn for i, c in enumerate(out["cards"]) if i != slot)
    # The posts, every one held for review, the video posts noting the run's frozen features; the template
    # still re-checks clean.
    posts = {(p["platform"], p["format"]): p for p in world.posts()}
    assert set(posts) == set(out["post_formats"].items())
    assert all(p["status"] == "pending_review" for p in posts.values())
    assert {(platform, fmt) for (platform, fmt), p in posts.items() if "video_features" in p["metadata"]} == {
        key for key in posts if key[1] == "video"}
    assert all(p["metadata"]["video_features"] == ["data_card", "push_in"]
               for (_pl, fmt), p in posts.items() if fmt == "video")
    assert news_templates.revalidate(out, fact_sheet=script["fact_sheet"], run_date=world.run_date) == []


@pytest.mark.parametrize("series", ["congress_count", "company_stakes", "earnings", "theme_explainer"])
def test_every_2b_layout_with_every_feature_on_is_drawn_and_allowed_through_the_real_app(monkeypatch, series):
    """The drop-2b layouts (`spotlight`, `pair`, `rows`, `grid`) with every feature on, through the real build,
    the real worker (it declares layouts_2b and video_v2) and the real server: every 2b sample's picture is
    legible in the card's text zone (the 8-tile grid included), so the DATA card replaces the card the
    read-back names — the series' decided FACT card (CARDS-B2-1) — while every hedge, caveat and source card
    stays on screen, declaring only strings the server allows for THIS run — every day reaches media_ready,
    posts held."""
    pytest.importorskip("PIL")
    from app.services.marketing import template_onscreen as tos

    record, day, _klass, layout, _asked = _2b_day(series)
    _theme_calendar(monkeypatch, series)
    monkeypatch.setattr(settings, "MARKETING_FIRST_COMMENT_PLATFORMS", "")
    world = TemplateWorld(monkeypatch, news=_StubNews2b(series, record), run_date=day, news_series=_EVERY_SERIES,
                          audio_seconds=7.0)
    _theme_calendar(monkeypatch, series)                                 # the world pins the default calendar
    monkeypatch.setattr(settings, "MARKETING_VIDEO_FEATURES", _ALL_FEATURES)
    ffmpeg = _real_render(world, monkeypatch)
    readbacks = _readbacks(world)
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready"
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    out = script["output"]
    if series in tos.NO_DATA_CARD_SERIES:
        # news-v23 (the restyle, 2026-11-19): the Congress Count, earnings and company stakes are ONE list of several
        # companies whose video COVER is that list — no data card is frozen, none is named on the wire, every card
        # stays a text card
        assert out["image_spec"]["layout"] == layout and out["video_features"] == ["push_in"]
        assert all(r["data_card_slot"] is None for r in readbacks)
        (video,) = _ready(world, "video")
        assert video["metadata"]["card_kinds"] == ["opening"] + ["text"] * len(out["cards"]) + ["disclaimer"]
        drawn = set(video["metadata"]["onscreen_text"])
        # the list cover draws the picture's footer line under its list (the server's list-cover allowance)
        allowed = (set(tos.opening_strings(out["opening_card"], out["logos"])) | set(mrs.VIDEO_BRAND_TEXT)
                   | {c[k] for c in out["cards"] for k in ("title", "body")} | {out["disclaimer_card"]}
                   | {out["image_footer"]})
        assert drawn <= allowed and out["opening_card"]["headline"] in drawn
        assert world.posts() and all(p["status"] == "pending_review" for p in world.posts())
        return
    assert out["image_spec"]["layout"] == layout and out["video_features"] == ["data_card", "push_in"]
    # The slot on the wire is the server's choice from what it stored: the decided fact card per series.
    slot = readbacks[-1]["data_card_slot"]
    assert type(slot) is int and all(r["data_card_slot"] == slot for r in readbacks)
    assert slot == tos.data_card_slot(series, out["cards"], mrs.data_card_strings(out))
    # news-v21: an earnings post has no "Revenue" card (the revenue estimate's basis is unknown): its date card
    decided = {"congress_count": "Counted once", "company_stakes": "As of", "earnings": "Quarter ended"}
    assert out["cards"][slot]["title"] == decided[series]
    (call,) = ffmpeg.calls
    assert call["push_in"] is True
    (video,) = _ready(world, "video")
    kinds = video["metadata"]["card_kinds"]
    expected = ["opening", "text", "text", "text", "text", "disclaimer"]
    expected[slot + 1] = "data"
    assert kinds == expected and video["metadata"]["data_card_slot"] == slot
    drawn = set(video["metadata"]["onscreen_text"])
    picture = set(tos.image_strings(out["image_spec"], out["logos"])) | {out["image_footer"]}
    allowed = (set(tos.opening_strings(out["opening_card"], out["logos"])) | picture | set(mrs.VIDEO_BRAND_TEXT)
               | {c[k] for c in out["cards"] for k in ("title", "body")} | {out["disclaimer_card"]})
    assert drawn <= allowed and out["image_footer"] in drawn       # the picture, footer included
    # Every card the series keeps is on screen — the old "last text card" rule covered one on each of these.
    kept = _kept_cards(series, out["cards"])
    assert out["cards"][-1] in kept and out["cards"][slot] not in kept
    assert all({c["title"], c["body"]} <= drawn for c in kept)
    assert world.posts() and all(p["status"] == "pending_review" for p in world.posts())


# ── Batch 2 fix round (2026-10-20): a hook card stands alone; the data card's slot crosses the wire ─────────
#
# B2-COMPL-1: the writer scans the hook beside its first narration line, so a closing claim that line debunks
# passes ("The market always recovers." → "Not always. …"). A hook card shows the hook ALONE (card 0, its
# captions hidden, the platforms' pinned 500 ms cover), so such a hook never gets one: the real write freezes
# the push-in only, the real read-back drops a hook card frozen before that check, and the real server refuses
# the hook on screen even then. CARDS-B2-1: the real worker draws a data card only at the slot the read-back
# names (the tests above follow it there); a read-back naming none, or one outside its cards, keeps today's
# text cards — never a guess at the last card — and the real server accepts that video.

#: A lesson the writer accepted only BESIDE its first line: alone, the hook makes the promise line 0 debunks.
_LEANING_LESSON = {**_LESSON, "hook": "The market always recovers.",
                   "video_script": ["Not always. Some markets took decades to come back.",
                                    *_LESSON["video_script"][1:]]}


@pytest.mark.parametrize("frozen_by", ["write", "hand_edited_row"])
def test_a_lesson_whose_hook_leans_on_its_first_line_opens_without_a_hook_card_through_the_real_app(
        monkeypatch, caplog, frozen_by):
    """Every feature on and the real worker declaring video_v2: the REAL write freezes the push-in but no hook
    card (WARNING) — and a row stored with one anyway (frozen before the check, or hand-edited) is read back
    without it (ERROR). Either way the wire carries ["push_in"], the real render opens on today's first card,
    pushing in, the hook captioned under it, and the real server records the posts held for review."""
    pytest.importorskip("PIL")
    from app.services.marketing import compliance

    hook, line0 = _LEANING_LESSON["hook"], _LEANING_LESSON["video_script"][0]
    # The case: compliant beside its debunk (what the writer accepted), not alone (what a hook card shows).
    assert compliance.scan_text("hook", hook, next_text=line0) == [] and compliance.scan_text("hook", hook)
    monkeypatch.setattr(settings, "MARKETING_FIRST_COMMENT_PLATFORMS", "")
    if frozen_by == "write":
        world = World(monkeypatch, _LEANING_LESSON, audio_seconds=_LESSON_AUDIO_SECONDS, freeze_on_seed=True)
    else:
        world = World(monkeypatch, {**_LEANING_LESSON, "video_features": ["hook_card", "push_in"]},
                      audio_seconds=_LESSON_AUDIO_SECONDS)
    monkeypatch.setattr(settings, "MARKETING_VIDEO_FEATURES", _ALL_FEATURES)
    ffmpeg = _real_render(world, monkeypatch)
    readbacks = _readbacks(world)
    with caplog.at_level(logging.INFO):
        assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready"
    assert "video_v2" in run["metadata"]["worker_capabilities"]
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    stored = script["output"]["video_features"]
    assert script["output"]["hook"] == hook
    assert stored == (["push_in"] if frozen_by == "write" else ["hook_card", "push_in"])
    # Said by the side that dropped it — the write (WARNING) or the read-back (ERROR) — naming the codes, never
    # the model's text.
    said = [r for r in caplog.records if r.name == ss.logger.name
            and "passes the compliance checks only beside its first narration line" in r.getMessage()]
    assert said and {r.levelno for r in said} == {logging.WARNING if frozen_by == "write" else logging.ERROR}
    assert not [r for r in said if hook in r.getMessage() or line0 in r.getMessage()]
    # The wire never carries the hook card; the push-in still goes.
    assert readbacks and all(r["video_features"] == ["push_in"] and r["data_card_slot"] is None for r in readbacks)
    # The real render: today's opening — the first text card from frame 0, pushing in — the hook captioned.
    (call,) = ffmpeg.calls
    assert call["push_in"] is True and [s.card for s in call["segments"]] == [0, 1, 2, 3]
    assert call["segments"][0].start == 0
    assert "recovers." in _dialogue(call["ass"])
    (video,) = _ready(world, "video")
    md = video["metadata"]
    assert md["video_features"] == ["push_in"] and md["push_in"] is True and md["data_card_slot"] is None
    assert md["card_kinds"] == ["text", "text", "text", "disclaimer"]
    first, *rest = _LESSON["cards"]
    assert md["onscreen_text"] == [first["title"], first["body"], *_LESSON_CHROME,
                                   *[c[k] for c in rest for k in ("title", "body")],
                                   _LESSON["disclaimer_card"], _STORE_CTA]
    # The posts, held for review; the video post's note is the run's FROZEN list (what the run asked for —
    # the video asset's own metadata says what was drawn).
    posts = {(p["platform"], p["format"]): p for p in world.posts()}
    assert set(posts) == {("tiktok", "video"), ("x", "text")}
    assert all(p["status"] == "pending_review" for p in posts.values())
    assert posts[("tiktok", "video")]["metadata"]["video_features"] == stored


def test_a_hook_card_drawn_anyway_on_a_row_frozen_before_the_check_is_refused_by_the_real_server(monkeypatch,
                                                                                              caplog):
    """The row keeps `hook_card` but its hook cannot stand alone: a worker that draws the hook card anyway (the
    read-back did not send it) is refused 422 by the real on-screen check, which says why (ERROR, the codes) —
    the flag alone never allows the hook on screen; nothing is posted."""
    from marketing import cards

    world = World(monkeypatch, {**_LEANING_LESSON, "video_features": ["hook_card", "push_in"]})
    monkeypatch.setattr(cards, "video_features", lambda script: ("hook_card", "push_in"))
    refused: List[Dict[str, Any]] = []

    def record(request, response):
        if request.method == "POST" and request.url.path.endswith("/assets"):
            body = json.loads(request.content)
            if body.get("kind") == "video":
                refused.append({"status": response.status_code, "drawn": body["metadata"]["onscreen_text"]})
        return response

    world.after = record
    with caplog.at_level(logging.ERROR, logger=mrs.logger.name):
        assert world.worker.main() == 1
    run = world._run_row()
    assert run["status"] == "failed" and "MARKETING_REQUEST_INVALID" in (run["last_error"] or "")
    assert world.assets("video") == [] and world.posts() == []
    assert [r["status"] for r in refused] == [422] and refused[0]["drawn"][0] == _LEANING_LESSON["hook"]
    assert any("hook_card is frozen, but the accepted hook passes the compliance checks only beside its first "
               "narration line" in r.getMessage() and run["id"] in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("tamper, level, said", [
    ("absent", logging.WARNING, "names no data_card_slot"),       # a web that predates the slot
    ("out_of_range", logging.ERROR, "is not an index of its"),    # one past its last card
], ids=["absent", "out_of_range"])
def test_a_read_back_without_a_usable_slot_keeps_every_text_card_and_the_real_server_accepts_it(
        monkeypatch, caplog, tamper, level, said):
    """data_card frozen and sent, but the read-back's `data_card_slot` stripped on the wire or outside its
    cards: the real worker draws NO data card — never the last card by default — says why, and keeps today's
    text cards (push-in still on); the real server, which allows a frozen feature's strings but never requires
    them, accepts that video and records the posts."""
    pytest.importorskip("PIL")
    from app.services.marketing import template_onscreen as tos

    monkeypatch.setattr(settings, "MARKETING_FIRST_COMMENT_PLATFORMS", "")
    # 2026-11-20: the noted roundup (`_noted_news`): its picture's note is the one string its list cover never draws
    world = TemplateWorld(monkeypatch, audio_seconds=7.0, news=_noted_news())
    monkeypatch.setattr(settings, "MARKETING_VIDEO_FEATURES", _ALL_FEATURES)
    ffmpeg = _real_render(world, monkeypatch)
    tampered: List[Dict[str, Any]] = []

    def tamper_slot(request, response):
        if request.method == "POST" and request.url.path.endswith("/script") and response.status_code == 200:
            body = json.loads(response.content)
            if body.get("status") == "accepted":
                assert type(body["script"]["data_card_slot"]) is int       # what the real web sent
                if tamper == "absent":
                    body["script"].pop("data_card_slot")
                else:
                    body["script"]["data_card_slot"] = len(body["script"]["cards"])
                tampered.append(body["script"])
                return httpx.Response(response.status_code, json=body)
        return response

    world.after = tamper_slot
    with caplog.at_level(logging.INFO):
        assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready"
    assert tampered and all(s["video_features"] == ["data_card", "push_in"] for s in tampered)
    (call,) = ffmpeg.calls
    assert call["push_in"] is True
    (video,) = _ready(world, "video")
    md = video["metadata"]
    assert md["card_kinds"] == ["opening", *["text"] * len(tampered[-1]["cards"]), "disclaimer"]
    assert md["data_card_slot"] is None and md["video_features"] == ["data_card", "push_in"]
    # no picture drawn: none of the picture's OWN strings (news-v23: its short footer is on every card, so the footer
    # alone no longer tells)
    picture_only = (set(tos.image_strings(tampered[-1]["image_spec"], tampered[-1]["logos"]))
                    - set(tos.opening_strings(tampered[-1]["opening_card"], tampered[-1]["logos"]))
                    - {tampered[-1]["image_footer"]})
    assert picture_only and not picture_only & set(md["onscreen_text"])
    records = [r for r in caplog.records if r.name == "marketing.cards" and said in r.getMessage()]
    assert records and {r.levelno for r in records} == {level}
    assert world.posts() and all(p["status"] == "pending_review" for p in world.posts())



# ══ Batch 3 (2026-10-20): Facebook and LinkedIn post the day's video too — the UNCHANGED worker, the real app ═════
#
# MARKETING_VIDEO_EXTRA_PLATFORMS lists facebook and linkedin (a web switch; the worker image is unchanged). The REAL
# write path (`_frozen_output`, run on the seeded lesson: `freeze_on_seed`) freezes the extras into the accepted
# output — the switch read at WRITE time — beside the run's video features (the real worker's claim declared
# video_v2). The read-back never carries them, so the worker renders today's one video and names only the PRIMARY
# posts; the REAL server's create_posts appends the two video posts itself, on the run's verified video, with the
# primary video post's video-features stamp, and answers them to the worker, which closes the day media_ready.
# With the switch empty the day is the switch-on day minus exactly the extras.
#
# Mutation-checked on 2026-10-20 (scratch plugins, the repo never edited): the first test goes red when the extra
# caption keeps the text disclaimer, an extra loses the video-features stamp or its first comment, or the extras are
# recorded before the worker's specs; the twin when the write-time reader ignores the switch or create_posts appends
# extras nothing froze.


def _b3_world(monkeypatch, extras: str):
    """A lesson day composed for TODAY (REAL captions: an extra video caption re-derives only from one) — TikTok's
    video, X's text, Facebook's and LinkedIn's text posts — stored through the REAL write path with
    MARKETING_VIDEO_EXTRA_PLATFORMS = `extras`, the push-in on and Facebook's first comment listed. Records what
    crossed the wire: every accepted read-back the worker received, and its create_posts request with the answer."""
    import os
    from datetime import date

    from test_marketing_run_service import _b3_posts

    monkeypatch.setattr(settings, "MARKETING_FIRST_COMMENT_PLATFORMS", "facebook")
    world = World(monkeypatch, _LESSON, freeze_on_seed=True)
    # Composed for the very date the worker will claim (World's MARKETING_RUN_DATE), never a second clock read.
    run_date = date.fromisoformat(os.environ["MARKETING_RUN_DATE"])
    world.package = {**_LESSON, "posts": _b3_posts(("tiktok", "x", "facebook", "linkedin"), run_date=run_date)}
    monkeypatch.setattr(settings, "MARKETING_VIDEO_EXTRA_PLATFORMS", extras)
    monkeypatch.setattr(settings, "MARKETING_VIDEO_FEATURES", "push_in")
    wire: Dict[str, List[Any]] = {"readbacks": [], "posts": []}

    def record(request, response):
        if request.method == "POST" and request.url.path.endswith("/script") and response.status_code == 200:
            body = json.loads(response.content)
            if body.get("status") == "accepted":
                wire["readbacks"].append(body["script"])
        if request.method == "POST" and request.url.path.endswith("/posts"):
            wire["posts"].append((json.loads(request.content), response.status_code, json.loads(response.content)))
        return response

    world.after = record
    return world, wire, run_date


def _b3_day(monkeypatch, extras: str) -> Dict[str, Any]:
    """One `_b3_world` tick to media_ready: what was stored, what crossed the wire, what was recorded."""
    world, wire, run_date = _b3_world(monkeypatch, extras)
    assert world.worker.main() == 0
    run = world._run_row()
    assert run["status"] == "media_ready" and run["stage"] == "assets_ready" and world.renders == 1
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    ((request, status, answer),) = wire["posts"]
    assert status == 200
    (video,) = _ready(world, "video")
    return {"world": world, "run": run, "run_date": run_date, "out": script["output"], "video": video,
            "readbacks": wire["readbacks"], "request": request["posts"], "answer": answer["posts"]}


def _b3_shape(rows: List[Dict[str, Any]], video_id: str) -> List[Dict[str, Any]]:
    """Post rows without what differs between two runs: ids and timestamps, and the run's own video id."""
    keep = ("platform", "format", "status", "title", "caption", "idempotency_key", "metadata", "approved_by")
    return [{**{k: r.get(k) for k in keep},
             "asset_ids": ["<video>" if a == video_id else a for a in r.get("asset_ids") or []]} for r in rows]


def test_batch3_the_unchanged_worker_gets_the_extra_video_posts_from_create_posts(monkeypatch):
    from app.schemas.marketing import EXTRA_FORMATS_KEY, VIDEO_POSTS_KEY
    from app.services.marketing import post_copy

    day = _b3_day(monkeypatch, "facebook,linkedin")
    out, video, run_date = day["out"], day["video"], day["run_date"]
    assert day["run"]["metadata"]["video_asset_id"] == video["id"]
    # The write froze the extras — the switch read at WRITE time — and the push-in (the claim declared video_v2) …
    assert out["post_formats"] == {"facebook": "text", "linkedin": "text", "tiktok": "video", "x": "text"}
    assert out[EXTRA_FORMATS_KEY] == {"facebook": "video", "linkedin": "video"}
    reel = {p: post_copy.extra_video_caption(p, out["posts"][p]["caption"], run_date) for p in ("facebook", "linkedin")}
    assert out[VIDEO_POSTS_KEY] == {p: {"title": None, "caption": c} for p, c in reel.items()}
    assert out["video_features"] == ["push_in"]
    # … which the worker never sees: every read-back is the primary day, so it renders today's one video …
    assert day["readbacks"] and all(not {EXTRA_FORMATS_KEY, VIDEO_POSTS_KEY} & set(r) for r in day["readbacks"])
    assert all(r["post_formats"] == out["post_formats"] for r in day["readbacks"])
    # … and names the PRIMARY posts only. The real server appends the two video posts to its answer.
    named = [(s["platform"], s["format"]) for s in day["request"]]
    assert sorted(named) == sorted(out["post_formats"].items())
    assert [(p["platform"], p["format"]) for p in day["answer"]] == named + [("facebook", "video"), ("linkedin", "video")]
    by = {(p["platform"], p["format"]): p for p in day["answer"]}
    tiktok = by[("tiktok", "video")]
    for platform in ("facebook", "linkedin"):
        p, md = by[(platform, "video")], by[(platform, "video")]["metadata"]
        assert p["asset_ids"] == tiktok["asset_ids"] == [video["id"]]                  # the run's one verified video
        assert (p["status"], p["title"], p["caption"]) == ("pending_review", None, reel[platform])
        # the platform's own caption with ONLY its disclaimer swapped for the narrated one (never the expectation
        # computed by the very function under test alone)
        plain, narrated = post_copy.disclaimer_for(platform, run_date), post_copy.disclaimer_for(platform, run_date,
                                                                                                 video=True)
        assert p["caption"] == out["posts"][platform]["caption"][:-len(plain)] + narrated
        assert "Script and narration generated with AI." in p["caption"] and "Written with AI" not in p["caption"]
        assert p["idempotency_key"] == f"{run_date.isoformat()}:{platform}:video"
        assert md["video_features"] == tiktok["metadata"]["video_features"] == ["push_in"]
        assert md["made_with_ai"] is True and md["dry_run"] is tiktok["metadata"]["dry_run"] is True
        assert md.get("first_comment") == (post_copy.SEARCH_LINE if platform == "facebook" else None)
        assert "image_fallback" not in md
        # beside the platform's own text post, which keeps its accepted caption and carries no video stamp
        text = by[(platform, "text")]
        assert text["caption"] == out["posts"][platform]["caption"] and "video_features" not in text["metadata"]
    assert len(day["world"].posts()) == 6 and day["run"]["metadata"]["posts_recorded"] == 6


def test_batch3_switch_empty_unchanged(monkeypatch):
    """MARKETING_VIDEO_EXTRA_PLATFORMS empty — its default — on the same lesson day: no extra key is stored and the
    server records exactly the worker's posts. Run beside the switch-on day, it is that day minus exactly the extras:
    the same stored output but the two keys, the same read-backs (the worker cannot tell the two days apart), the
    same create_posts request, the same primary rows."""
    from app.schemas.marketing import EXTRA_FORMATS_KEY, VIDEO_POSTS_KEY

    off = _b3_day(monkeypatch, "")
    assert off["readbacks"] and not {EXTRA_FORMATS_KEY, VIDEO_POSTS_KEY} & set(off["out"])
    named = [(s["platform"], s["format"]) for s in off["request"]]
    assert [(p["platform"], p["format"]) for p in off["answer"]] == named and len(named) == 4
    assert len(off["world"].posts()) == 4 and off["run"]["metadata"]["posts_recorded"] == 4
    on = _b3_day(monkeypatch, "facebook,linkedin")
    assert {k: v for k, v in on["out"].items() if k not in (EXTRA_FORMATS_KEY, VIDEO_POSTS_KEY)} == off["out"]
    assert on["readbacks"] == off["readbacks"]
    swap = lambda specs, vid: [{**s, "asset_ids": ["<video>" if a == vid else a for a in s.get("asset_ids") or []]}
                               for s in specs]
    assert swap(on["request"], on["video"]["id"]) == swap(off["request"], off["video"]["id"])
    assert _b3_shape(on["answer"][:4], on["video"]["id"]) == _b3_shape(off["answer"], off["video"]["id"])


# ══ The spotlight lane (migration 210, 2026-11-15): the real worker's tick plan against the real app ══════════════
# The worker's ET clock is frozen (no MARKETING_FORCE, no MARKETING_RUN_DATE), so each tick is exactly the plan the
# cron would run: yesterday's resumes before a lane's hour, then today's main, then today's spotlight — one pipeline
# per tick. Everything behind the two claim routes is real (the router, the claim gate, the ledger, the build).


def _claim_log(world, mark: int) -> List[tuple]:
    """(lane route, run_date, resume_only) of every claim the worker sent since `mark`, as the server received it."""
    out = []
    for method, _host, path in world.calls[mark:]:
        if method == "POST" and path.endswith("/claim"):
            out.append(path.split("/internal/marketing/", 1)[1])
    return out


def _tick(world, monkeypatch, day, hour: int):
    """One unforced worker tick at `hour`:15 ET on `day`; (exit code, the claim routes it called, their bodies)."""
    bodies: List[Dict[str, Any]] = []
    previous = world.before

    def record(request):
        if request.method == "POST" and request.url.path.endswith("/claim"):
            bodies.append(json.loads(request.content))
        return previous(request) if previous is not None else None

    class Frozen(world.worker.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(day.year, day.month, day.day, hour, 15, tzinfo=_ET)

    monkeypatch.setattr(world.worker, "datetime", Frozen)
    world.before = record
    mark = len(world.calls)
    try:
        code = world.worker.main()
    finally:
        world.before = previous
    return code, _claim_log(world, mark), bodies


def _rows_by_lane(world) -> Dict[str, Dict[str, Any]]:
    rows = world.fake.tables[mrs.RUNS].rows
    by = {mrs.run_lane(r): r for r in rows}
    assert len(by) == len(rows), rows               # never two rows of one lane on one day
    return by


def test_spotlight_with_the_switch_off_the_second_claim_is_not_scheduled_and_writes_nothing(world):
    """MARKETING_SPOTLIGHT_ENABLED off (its default, pinned by `World`): the first tick runs the main day; the next
    finds it done, asks the spotlight lane through its own route — the real gate answers `not_scheduled` before any
    read or write — and exits 0 with one run row on the day."""
    assert world.worker.main() == 0
    assert world._run_row()["status"] == "media_ready"
    mark = len(world.calls)
    assert world.worker.main() == 0
    assert _claim_log(world, mark) == ["runs/claim", "lanes/spotlight/claim"]
    assert [c for c in world.calls[mark:] if not c[2].endswith("/claim")] == []      # nothing after the two claims
    (row,) = world.fake.tables[mrs.RUNS].rows
    assert mrs.run_lane(row) == "main" and "lane" not in row      # the main INSERT never sends a lane


class _StubNewsBySeries:
    """The adapter's public surface: each series in `answers` answers its one record; every other comes up empty."""

    MarketingNewsUnavailable = _StubNews.MarketingNewsUnavailable

    def __init__(self, answers: Dict[str, Any]) -> None:
        self.answers = answers
        self.calls: List[str] = []
        self.logo_calls: List[str] = []

    async def candidates(self, series, *, run_date, exclude, limit, deadline, **kw):
        # `kw`: the insider spotlight's ledger inputs (spotlight_checked_through, spotlight_recent) — that series only
        from types import SimpleNamespace

        self.calls.append(series)
        record = self.answers.get(series)
        if record is not None:
            return SimpleNamespace(series=series, records=(record,), skip_reason=None, rejections={})
        return SimpleNamespace(series=series, records=(), skip_reason=f"{series}_none_qualified", rejections={})

    async def fetch_logo(self, symbol, *, max_bytes, timeout):
        self.logo_calls.append(symbol)
        return _logo_png(symbol), "image/png"


def _costco_money_map():
    from datetime import date

    from app.services.marketing import company_news_rules as R

    return R.MoneyMap(series="money_map", company=R.CompanyRef(symbol="COST", name="Costco"), fiscal_year="2025",
                      period_end=date(2025, 8, 31),
                      segments=(R.Segment("United States", 200_000_000_000.0), R.Segment("Canada", 38_500_000_000.0),
                                R.Segment("Other International", 36_700_000_000.0)),
                      other_usd=None, eliminations_usd=None, revenue_usd=275_200_000_000.0,
                      gross_profit_usd=35_400_000_000.0, operating_profit_usd=10_400_000_000.0,
                      net_income_usd=8_100_000_000.0)


def test_spotlight_a_two_lane_day_runs_each_lane_at_its_hour_under_its_own_paths_and_keys(monkeypatch):
    """A Thursday spotlight day (the default Tue/Thu/Sat), the lane on with the insider spotlight listed, the worker on
    its real clock:

    * 10:15 — yesterday's MAIN run is asked for a resume (none), then today's SPOTLIGHT run is claimed through its own
      route and runs to media_ready: the insider spotlight (Contoso; Money Map is the MAIN lane's series that Thursday,
      so it never enters the spotlight's chain — C0, 2026-11-16), its objects under `<date>/spotlight/`, its posts
      keyed `<date>:spotlight:<platform>:<format>`, its manifest naming the lane;
    * 16:15 — today's MAIN run is claimed and is the tick's only pipeline (the spotlight lane is not even asked):
      Costco's Money Map, under the paths and keys the main lane always had, its INSERT and manifest without a lane;
    * 17:15 — both runs are done: two claims, nothing else, no new row.
    Two runs, two scripts, no shared storage path and no shared post key."""
    pytest.importorskip("PIL")
    import test_marketing_news_templates as NT

    day = NT.SPOT_THU
    assert day.weekday() == 3
    news = _StubNewsBySeries({"insider_spotlight": NT.isp_cluster(), "money_map": _costco_money_map()})
    world = TemplateWorld(monkeypatch, news=news, run_date=day,
                          news_series="ceo_buys,insider_buys,thirteen_f,money_map,insider_spotlight")
    for name, value in (("MARKETING_SPOTLIGHT_ENABLED", True), ("MARKETING_SPOTLIGHT_WEEKDAYS", "tue,thu,sat")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.delenv("MARKETING_FORCE")
    monkeypatch.delenv("MARKETING_RUN_DATE")
    d, y = day.isoformat(), "2026-11-18"

    # ── 10:15 ET: the spotlight run ──
    code, claims, bodies = _tick(world, monkeypatch, day, 10)
    assert code == 0
    assert claims == ["runs/claim", "lanes/spotlight/claim"]
    assert [(b["run_date"], b["resume_only"]) for b in bodies] == [(y, True), (d, False)]
    assert bodies[0]["claim_nonce"] != bodies[1]["claim_nonce"]
    spot = _rows_by_lane(world)["spotlight"]
    assert list(_rows_by_lane(world)) == ["spotlight"]
    assert (spot["run_date"], spot["status"], spot["stage"]) == (d, "media_ready", "assets_ready")
    (spot_script,) = world.fake.tables[mrs.SCRIPTS].rows
    assert (spot_script["lane"], spot_script["status"], spot_script["template_id"]) == ("spotlight", "accepted",
                                                                                         "insider_spotlight")
    assert spot_script["fact_sheet"]["selection"]["plan"] == "spotlight_13f" and news.calls == ["insider_spotlight"]
    assert "money_map" not in spot_script["fact_sheet"]["selection"]["chain"]
    spot_assets = [a for a in world.assets() if a["run_id"] == spot["id"]]
    assert {a["kind"] for a in spot_assets} >= {"manifest", "audio", "video", "card"}
    assert all(a["storage_path"].startswith(f"{d}/spotlight/") for a in spot_assets), spot_assets
    (manifest,) = [a for a in spot_assets if a["kind"] == "manifest"]
    assert json.loads(world.stored[manifest["storage_path"]])["lane"] == "spotlight"
    spot_posts = [p for p in world.posts() if p["run_id"] == spot["id"]]
    assert spot_posts and all(p["idempotency_key"] == f"{d}:spotlight:{p['platform']}:{p['format']}"
                              and p["status"] == "pending_review" and p["metadata"]["series"] == "insider_spotlight"
                              for p in spot_posts)

    # ── 16:15 ET: the main run, the tick's only pipeline ──
    code, claims, bodies = _tick(world, monkeypatch, day, 16)
    assert code == 0
    assert claims == ["runs/claim"] and [(b["run_date"], b["resume_only"]) for b in bodies] == [(d, False)]
    rows = _rows_by_lane(world)
    main = rows["main"]
    assert "lane" not in main                      # the main INSERT never sent a lane: the column default applies
    assert (main["run_date"], main["status"], main["template_id"]) == (d, "media_ready", "money_map")
    main_script = next(s for s in world.fake.tables[mrs.SCRIPTS].rows if s["run_id"] == main["id"])
    assert "lane" not in main_script and main_script["template_id"] == "money_map"
    assert news.calls == ["insider_spotlight", "money_map"]
    main_assets = [a for a in world.assets() if a["run_id"] == main["id"]]
    assert main_assets and all(a["storage_path"].startswith(f"{d}/") and "/spotlight/" not in a["storage_path"]
                               for a in main_assets)
    (main_manifest,) = [a for a in main_assets if a["kind"] == "manifest"]
    assert "lane" not in json.loads(world.stored[main_manifest["storage_path"]])
    main_posts = [p for p in world.posts() if p["run_id"] == main["id"]]
    assert main_posts and all(p["idempotency_key"] == f"{d}:{p['platform']}:{p['format']}" for p in main_posts)
    # the two runs of the day share no object and no post key
    assert not {a["storage_path"] for a in spot_assets} & {a["storage_path"] for a in main_assets}
    assert not {p["idempotency_key"] for p in spot_posts} & {p["idempotency_key"] for p in main_posts}

    # ── 17:15 ET: both done ──
    posts_before, rows_before = len(world.posts()), len(world.fake.tables[mrs.RUNS].rows)
    mark = len(world.calls)
    code, claims, _bodies = _tick(world, monkeypatch, day, 17)
    assert code == 0 and claims == ["runs/claim", "lanes/spotlight/claim"]
    assert [c for c in world.calls[mark:] if not c[2].endswith("/claim")] == []
    assert (len(world.posts()), len(world.fake.tables[mrs.RUNS].rows)) == (posts_before, rows_before)


def test_spotlight_a_manual_run_of_one_lane_never_claims_the_other(monkeypatch):
    """MARKETING_RUN_LANE=spotlight with MARKETING_FORCE (a manual run): only the lane route is called, through the
    real gate, and only the spotlight row exists afterwards."""
    pytest.importorskip("PIL")
    from datetime import date

    day = date(2026, 11, 21)              # a Saturday: the main day is the lesson alone, so Money Map is the spotlight's
    news = _StubNewsBySeries({"ceo_buys": _StubNews(day).week, "money_map": _costco_money_map()})
    world = TemplateWorld(monkeypatch, news=news, run_date=day)
    for name, value in (("MARKETING_SPOTLIGHT_ENABLED", True), ("MARKETING_SPOTLIGHT_WEEKDAYS", "sat")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setenv("MARKETING_RUN_LANE", "spotlight")
    assert world.worker.main() == 0
    assert _claim_log(world, 0) == ["lanes/spotlight/claim"]
    assert list(_rows_by_lane(world)) == ["spotlight"] and news.calls == ["money_map"]


# ── workstream C (2026-11-16): per-post ART, the real build → the real worker → the real server ─────────────────────
#
# Owner decisions 2026-11-20 ("Drop it from insider posts (Recommended)"; "3 - 5 companies at once"): a Form 4 week —
# three companies at least — gets NO art with MARKETING_ART_ENABLED on: the REAL build records `not_a_company_series`
# before any spend (neither fake client is reached), nothing is stored or downloaded, the REAL worker draws its list
# cover and picture without art and declares none, and create_posts notes the failed record (drawn nowhere, nothing
# made with AI but the video). The one art series left, the Money Map's cover, is
# test_post_art_a_main_lane_money_map_day_draws_its_art_on_the_cover_only below.


class _StubNewsOne(_StubNews):
    """`_StubNews` (the three-company CEO week) plus the adapter's `company_facts` (never read: no art)."""

    def __init__(self, run_date) -> None:
        super().__init__(run_date)
        self.facts_calls: List[str] = []

    async def company_facts(self, symbol, *, timeout):
        self.facts_calls.append(symbol)
        return {"sector": "Consumer Cyclical", "industry": "Home Improvement Retail"}


def test_post_art_a_form4_week_draws_no_art_through_the_real_app(monkeypatch):
    pytest.importorskip("PIL")
    import test_marketing_post_art as PA

    from app.services import llm_provider as lp
    from app.services.marketing import post_art as pa
    from app.services.marketing import template_onscreen as tos
    from marketing import cards
    from marketing import render as rd

    lp.reset_breaker()
    monkeypatch.setattr(lp, "_logged_once", set())
    monkeypatch.setattr(pa, "_logged_once", set())
    for name, value in {
        "MARKETING_ART_ENABLED": True, "MARKETING_ART_MONTHLY_BUDGET_USD": 30.0, "MARKETING_ART_BUDGET_SECONDS": 180,
        "MARKETING_ART_CLAUDE_MODEL": "claude-opus-5-5", "MARKETING_ART_IMAGE_MODEL": "gemini-3-pro-image",
        "MARKETING_ART_IMAGE_MICROS": 134_000, "CAYDEX_ANTHROPIC_API_KEY": "sk-ant-test",
        "CAYDEX_CLAUDE_MAX_TOKENS": 16000, "CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS": 120.0,
        "CAYDEX_CLAUDE_BREAKER_SECONDS": 3600.0,
    }.items():
        monkeypatch.setattr(settings, name, value)
    world = TemplateWorld(monkeypatch, news=_StubNewsOne(mrs.run_date_et(datetime.now(timezone.utc))))
    gemini, claude = PA.FakeGemini(), PA.FakeClaude()
    world.scripts = ss.MarketingScriptService(world.ledger, writer=world._never_the_writer, news=world.news,
                                              art_gemini=gemini, art_claude=claude)
    covers: List[Any] = []
    template_video = world._produce_template_video

    def produce(**kw):
        covers.append(kw["specs"][0].art)
        # the cover really renders with its art (Pillow, no ffmpeg)
        cards.render_card(kw["specs"][0], font_path=str(Path(kw["fonts_dir"]) / "Inter-Bold.ttf"),
                          layout_engine=kw["layout_engine"])
        return template_video(**kw)

    monkeypatch.setattr(rd, "produce_video", produce)
    try:
        assert world.worker.main() == 0
    finally:
        lp.reset_breaker()
    run = world._run_row()
    assert run["status"] == "media_ready" and "post_art" in run["metadata"]["worker_capabilities"]
    (script,) = world.fake.tables[mrs.SCRIPTS].rows
    out = script["output"]
    art = out["art"]
    assert (art["status"], art["reason"]) == ("failed", "not_a_company_series") and out["series"] == "ceo_buys"
    assert len(out["image_spec"]["sections"][0]["rows"]) == 3 and pa.estimated_micros(art["usage"]) == 0
    assert world.news.facts_calls == [] and gemini.calls == [] and claude.calls == [] and gemini.text_calls == []
    # nothing stored, nothing downloaded; the cover (the list) and the picture drew no art and declare none
    assert not [k for k in world.stored if k.startswith("art/")]
    assert [u for u in world.downloads if "/art/" in u] == []
    (cover_art,) = covers
    assert cover_art is None
    (video,) = _ready(world, "video")
    vmd = video["metadata"]
    assert vmd["opening_art_sha256"] is None and cards.ART_LABEL not in vmd["onscreen_text"]
    card = _the_card(world)
    assert card["metadata"]["art_sha256"] is None and cards.ART_LABEL not in card["metadata"]["onscreen_text"]
    assert mrs.art_surfaces(out) == () and tos.art_allowed(None) == []
    # every post notes the failed record (drawn nowhere); only the video is made with AI
    posts = world.posts()
    assert posts and all(p["status"] == "pending_review" for p in posts)
    for p in posts:
        note = p["metadata"]["art"]
        assert (note["status"], note["reason"]) == ("failed", "not_a_company_series"), p["platform"]
        assert note["drawn"] == {"picture": False, "cover": False}, p["platform"]
        assert p["metadata"]["made_with_ai"] is (p["format"] == "video")


# ══ Post art (workstream C, slice C5, 2026-11-16): the REAL build → the REAL worker → the REAL server, per surface ══
#
# Owner decisions 2026-11-20 ("Drop it from insider posts (Recommended)"): the spotlight lane's insider LIST (3-5
# companies) draws NO art on either surface; the main lane's Money Map draws it on the cover only ("Video cover now,
# pictures later") — the one art surface left. The
# only fakes beside the world's are the two art clients (tests/test_marketing_post_art.py's, bound to the real
# clients' signatures). The REAL worker downloads the art from the fake bucket by its content address, draws it,
# declares what it drew; the REAL server's declaration checks accept exactly that and refuse a forged declaration
# (422); create_posts notes it on every post and marks the posts whose picture drew it as made with AI; the review
# bundle's decision text carries the "Art:" line. With the switch off, the day is the day of before, byte for byte.

_ART_SPOT_SERIES = "ceo_buys,insider_buys,thirteen_f,money_map,insider_spotlight"
_ART_SHA_RE = r"[0-9a-f]{64}"


def _art_settings(monkeypatch, *, enabled: bool = True) -> None:
    from app.services import llm_provider as lp
    from app.services.marketing import post_art as pa

    lp.reset_breaker()
    monkeypatch.setattr(lp, "_logged_once", set())
    monkeypatch.setattr(pa, "_logged_once", set())
    for name, value in {
        "MARKETING_ART_ENABLED": enabled, "MARKETING_ART_MONTHLY_BUDGET_USD": 30.0, "MARKETING_ART_BUDGET_SECONDS": 180,
        "MARKETING_ART_CLAUDE_MODEL": "claude-opus-5-5", "MARKETING_ART_IMAGE_MODEL": "gemini-3-pro-image",
        "MARKETING_ART_IMAGE_MICROS": 134_000, "CAYDEX_ANTHROPIC_API_KEY": "sk-ant-test",
        "CAYDEX_CLAUDE_MAX_TOKENS": 16000, "CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS": 120.0,
        "CAYDEX_CLAUDE_BREAKER_SECONDS": 3600.0,
    }.items():
        monkeypatch.setattr(settings, name, value)
    # owner decision 2026-11-20 ("use it nowhere for now"): no shipped series carries art — the kept art path is
    # exercised end to end on the Money Map, put back into the art set for the test only
    from test_marketing_post_art import keep_art_on_money_map
    keep_art_on_money_map(monkeypatch)


class _StubNewsArt(_StubNewsBySeries):
    """`_StubNewsBySeries` plus the adapter's `company_facts` (the art brief's sector / industry)."""

    def __init__(self, answers: Dict[str, Any]) -> None:
        super().__init__(answers)
        self.facts_calls: List[str] = []

    async def company_facts(self, symbol, *, timeout):
        self.facts_calls.append(symbol)
        return {"sector": "Technology", "industry": "Software Infrastructure"}


class _ArtDay:
    """One art day's world: `which` = "spotlight" (Thu 2026-11-19, the spotlight lane alone — MARKETING_RUN_LANE —
    answering the insider spotlight Contoso cluster) or "money_map" (Thu 2026-12-03, off-season: the main lane's
    Money Map, Costco). The art clients are the post-art suite's fakes; the faked ffmpeg call records the opening
    card's art and its REAL rendered PNG (Pillow) — the cover."""

    def __init__(self, monkeypatch, which: str, *, enabled: bool = True) -> None:
        from datetime import date

        import test_marketing_news_templates as NT
        import test_marketing_post_art as PA
        from marketing import cards
        from marketing import render as rd

        if which == "spotlight":
            self.day = NT.SPOT_THU
            self.news = _StubNewsArt({"insider_spotlight": NT.isp_cluster()})
            self.world = TemplateWorld(monkeypatch, news=self.news, run_date=self.day, news_series=_ART_SPOT_SERIES)
            for name, value in (("MARKETING_SPOTLIGHT_ENABLED", True), ("MARKETING_SPOTLIGHT_WEEKDAYS", "tue,thu,sat")):
                monkeypatch.setattr(settings, name, value)
            monkeypatch.setenv("MARKETING_RUN_LANE", "spotlight")
        else:
            self.day = date(2026, 12, 3)
            self.news = _StubNewsArt({"money_map": _costco_money_map()})
            self.world = TemplateWorld(monkeypatch, news=self.news, run_date=self.day)
        _art_settings(monkeypatch, enabled=enabled)
        self.gemini, self.claude = PA.FakeGemini(), PA.FakeClaude()
        w = self.world
        w.scripts = ss.MarketingScriptService(w.ledger, writer=w._never_the_writer, news=self.news,
                                              art_gemini=self.gemini, art_claude=self.claude)
        self.covers: List[Dict[str, Any]] = []
        template_video = w._produce_template_video

        def produce(**kw):
            cover = kw["specs"][0]
            png = cards.render_card(cover, font_path=str(Path(kw["fonts_dir"]) / "Inter-Bold.ttf"),
                                    layout_engine=kw["layout_engine"])
            self.covers.append({"kind": cover.kind, "art": cover.art, "png": png})
            return template_video(**kw)

        monkeypatch.setattr(rd, "produce_video", produce)

    def run(self) -> int:
        from app.services import llm_provider as lp

        try:
            return self.world.worker.main()
        finally:
            lp.reset_breaker()

    # ── views ──
    def script(self) -> Dict[str, Any]:
        (script,) = self.world.fake.tables[mrs.SCRIPTS].rows
        return script

    def picture(self) -> Dict[str, Any]:
        return _the_card(self.world)

    def video(self) -> Dict[str, Any]:
        (video,) = _ready(self.world, "video")
        return video

    def art_downloads(self) -> List[str]:
        return [u for u in self.world.downloads if "/marketing-media/art/" in u]


def test_post_art_an_insider_list_day_draws_no_art_through_the_real_app(monkeypatch):
    """Owner decisions 2026-11-20 ("Drop it from insider posts (Recommended)"): the spotlight lane's insider LIST, art
    switch on — the REAL build records `not_a_company_series` before any spend, the REAL worker draws the list picture
    and the compact list cover without art and declares none, the REAL server accepts that, every post notes the
    failed record, only the videos are made with AI, and the review line says why there is no art."""
    pytest.importorskip("PIL")
    from app.services.marketing import post_art as pa
    from app.services.marketing import review_service as rs
    from app.services.marketing import template_onscreen as tos
    from marketing import cards

    day = _ArtDay(monkeypatch, "spotlight")
    assert day.run() == 0
    w = day.world
    run = w._run_row()
    assert (mrs.run_lane(run), run["status"], run["stage"]) == ("spotlight", "media_ready", "assets_ready")
    assert "post_art" in run["metadata"]["worker_capabilities"]
    out = day.script()["output"]
    art = out["art"]
    assert (out["series"], out["image_spec"]["layout"]) == ("insider_spotlight", "rows")
    assert len(out["image_spec"]["sections"][0]["rows"]) >= 3
    assert (art["status"], art["reason"]) == ("failed", "not_a_company_series")
    assert pa.estimated_micros(art["usage"]) == 0 and mrs.art_surfaces(out) == ()
    assert day.news.facts_calls == [] and day.gemini.calls == [] and day.claude.calls == []
    assert day.art_downloads() == [] and not [k for k in w.stored if k.startswith("art/")]
    pmd = day.picture()["metadata"]
    assert pmd["art_sha256"] is None and tos.ART_LABEL not in pmd["onscreen_text"]
    (cover,) = day.covers
    assert cover["kind"] == "opening" and cover["art"] is None
    vmd = day.video()["metadata"]
    assert vmd["opening_art_sha256"] is None and cards.ART_LABEL not in vmd["onscreen_text"]
    posts = [p for p in w.posts() if p["run_id"] == run["id"]]
    assert posts and all(p["status"] == "pending_review" for p in posts)
    for p in posts:
        note = p["metadata"]["art"]
        assert (note["status"], note["drawn"]) == ("failed", {"picture": False, "cover": False}), p["platform"]
        assert p["metadata"]["made_with_ai"] is (p["format"] == "video"), (p["platform"], p["format"])
    assert rs.art_line(posts) == "Art: none today — this post is not about a single company"


def test_post_art_a_main_lane_money_map_day_draws_its_art_on_the_cover_only(monkeypatch):
    """"Video cover now, pictures later": the main lane's single-company Money Map keeps its own picture layout, byte
    for byte the no-art picture's, and carries the art on its video's opening card only."""
    pytest.importorskip("PIL")
    from app.services.marketing import review_service as rs
    from marketing import cards

    day = _ArtDay(monkeypatch, "money_map")
    assert day.run() == 0
    w = day.world
    run = w._run_row()
    assert (mrs.run_lane(run), run["status"], run["template_id"]) == ("main", "media_ready", "money_map")
    out = day.script()["output"]
    art = out["art"]
    assert (art["status"], art["surfaces"], art["company"]["symbol"]) == ("ok", ["cover"], "COST")
    assert out["image_spec"]["layout"] != "feature"
    # the picture: never offered the art (not a feature picture) — no declaration, no label
    pmd = day.picture()["metadata"]
    assert pmd["art_sha256"] is None and cards.ART_LABEL not in pmd["onscreen_text"] and "art_skipped" not in pmd
    # the cover drew it
    (cover,) = day.covers
    assert cover["art"] is not None and cover["art"].sha256 == art["sha256"]
    vmd = day.video()["metadata"]
    assert vmd["opening_art_sha256"] == art["sha256"] and cards.ART_LABEL in vmd["onscreen_text"]
    assert day.art_downloads() == [art["url"]]
    posts = w.posts()
    for p in posts:
        assert p["metadata"]["art"]["drawn"] == {"picture": False, "cover": True}, p["platform"]
        assert p["metadata"]["made_with_ai"] is (p["format"] == "video"), (p["platform"], p["format"])
    assert rs.art_line(posts).endswith(" · check passed · on: video cover")


@pytest.mark.parametrize("which, kind, forge, problem", [
    # owner decisions 2026-11-20: the Money Map's cover is the one art surface left — its VIDEO declarations
    # a declaration of another picture than the accepted art (the label kept)
    ("money_map", "video", lambda md: md.update(opening_art_sha256="f" * 64), "art_mismatch"),
    # the label burned without a declaration, and a declaration without the label
    ("money_map", "video", lambda md: md.update(opening_art_sha256=None), "art_label_without_art"),
    ("money_map", "video", lambda md: md.update(onscreen_text=[s for s in md["onscreen_text"]
                                                               if s != "AI illustration"]), "art_label_missing"),
    # the main lane's picture never carries art, even the accepted art with its label
    ("money_map", "card", "main_picture", "art_surface_not_allowed"),
])
def test_post_art_a_forged_declaration_is_refused_422_by_the_real_server(monkeypatch, which, kind, forge, problem):
    """The worker is the least-trusted process: a declaration that is not the accepted output's art on a surface the
    record allows — whatever the worker drew — is refused 422 by the real server, the run fails and nothing is
    posted."""
    pytest.importorskip("PIL")
    from app.services.marketing import template_onscreen as tos

    assert problem in tos.ART_DECLARATION_PROBLEMS or problem == "art_surface_not_allowed"
    day = _ArtDay(monkeypatch, which)
    w = day.world
    answered: List[Dict[str, Any]] = []

    def forged(request):
        if not (request.method == "POST" and request.url.path.endswith("/assets")):
            return None
        body = json.loads(request.content)
        md = body.get("metadata") or {}
        if body.get("kind") != kind or (kind == "card" and md.get("image_role") != "post_image"):
            return None
        if forge == "main_picture":
            accepted = day.script()["output"]["art"]["sha256"]
            md.update(art_sha256=accepted, onscreen_text=list(md["onscreen_text"]) + [tos.ART_LABEL])
        else:
            forge(md)
        r = w.server.request("POST", request.url.path, content=json.dumps(body),
                             headers={k: v for k, v in request.headers.items()
                                      if k.lower() not in ("host", "content-length")})
        answered.append({"status": r.status_code, "body": r.json()})
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})

    w.before = forged
    assert day.run() == 1
    run = w._run_row()
    assert run["status"] == "failed" and "MARKETING_REQUEST_INVALID" in (run["last_error"] or "")
    assert [a["status"] for a in answered] == [422]
    assert problem in json.dumps(answered[0]["body"]), answered[0]["body"]
    assert w.posts() == []
    assert _ready(w, "video" if kind == "video" else "card") == []


def test_post_art_with_the_switch_off_the_day_is_the_day_of_before_byte_for_byte(monkeypatch):
    """MARKETING_ART_ENABLED off (its default): no art step (neither client is reached), no `art` key in the stored
    output, no art read-back, no art download, no declaration, no label, no note on any post, the picture posts not
    made with AI. And it is a real KILL switch: art made at build time, then the switch turned off before the worker
    reads the script back, gives a picture and a cover byte for byte the switch-off day's."""
    pytest.importorskip("PIL")
    from marketing import cards

    off = _ArtDay(monkeypatch, "money_map", enabled=False)   # owner decisions 2026-11-20: the one art series
    assert off.run() == 0
    out = off.script()["output"]
    assert "art" not in out and off.gemini.calls == [] and off.claude.calls == []
    assert off.art_downloads() == []
    pmd, vmd = off.picture()["metadata"], off.video()["metadata"]
    assert pmd["art_sha256"] is None and vmd["opening_art_sha256"] is None
    assert cards.ART_LABEL not in pmd["onscreen_text"] + vmd["onscreen_text"]
    (cover_off,) = off.covers
    assert cover_off["art"] is None
    posts = off.world.posts()
    assert posts and not any("art" in p["metadata"] for p in posts)
    assert all(p["metadata"]["made_with_ai"] is (p["format"] == "video") for p in posts)
    picture_off = off.world.stored[off.picture()["storage_path"]]

    # the kill switch: the build makes art (switch on — the selection kick's read-back carries it), then the switch
    # goes off before the script stage's read-back, the one the worker keeps for its voice and render stages
    monkeypatch.undo()
    killed = _ArtDay(monkeypatch, "money_map", enabled=True)
    reads: List[Any] = []

    def switch_off_after_the_first_accepted_script(request, response):
        if request.method == "POST" and request.url.path.endswith("/script") and response.status_code == 200:
            body = json.loads(response.content)
            if body.get("status") == "accepted":
                reads.append(body["script"].get("art"))
                monkeypatch.setattr(settings, "MARKETING_ART_ENABLED", False)
        return response

    killed.world.after = switch_off_after_the_first_accepted_script
    assert killed.run() == 0
    assert killed.script()["output"]["art"]["status"] == "ok" and len(killed.gemini.calls) == 1
    assert len(reads) == 2 and reads[0] is not None and reads[0]["sha256"] == killed.script()["output"]["art"]["sha256"]
    assert reads[1] is None                       # read at call time: the switch is a real kill switch
    assert killed.art_downloads() == []
    kpmd, kvmd = killed.picture()["metadata"], killed.video()["metadata"]
    assert kpmd["art_sha256"] is None and kvmd["opening_art_sha256"] is None
    assert killed.world.stored[killed.picture()["storage_path"]] == picture_off
    (cover_killed,) = killed.covers
    assert cover_killed["art"] is None and cover_killed["png"] == cover_off["png"]
    # the note says the art was made but not drawn; nothing is marked made with AI for a picture without art
    for p in killed.world.posts():
        assert p["metadata"]["art"]["drawn"] == {"picture": False, "cover": False}
        assert p["metadata"]["made_with_ai"] is (p["format"] == "video")
