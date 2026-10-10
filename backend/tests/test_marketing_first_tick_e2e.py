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
"""

from __future__ import annotations

import email
import functools
import importlib.util
import json
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
    assert card["metadata"]["onscreen_text"] == [_IMAGE_POST["title"], *_IMAGE_POST["paragraphs"],
                                                 frozen["image_footer"]]
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
    prepared = outlet_x.ADAPTER.prepare(row)
    assert prepared.payload["image"]["asset_id"] == _the_card(world)["id"]
    assert prepared.payload["image"]["alt"].startswith(_IMAGE_POST["title"])
    assert prepared.pre_send_charge is not None                     # the alt text is billed apart
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
            row("SBUX", "Starbucks", 410_000.0, 4_850.0, end - __import__("datetime").timedelta(days=2))))
        self.answer = SimpleNamespace(series="ceo_buys", records=(self.week,), skip_reason=None, rejections={})
        self.calls: List[str] = []
        self.logo_calls: List[str] = []

    async def candidates(self, series, *, run_date, exclude, limit, deadline):
        self.calls.append(series)
        assert series == "ceo_buys", series
        return self.answer

    async def fetch_logo(self, symbol, *, max_bytes, timeout):
        self.logo_calls.append(symbol)
        return _logo_png(symbol), "image/png"


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
                 news_series: str = "ceo_buys,insider_buys,thirteen_f,money_map") -> None:
        from app.services.marketing import selection
        from marketing import render as rd

        monkeypatch.setattr(settings, "SUPABASE_URL", _SB_PUBLIC)
        super().__init__(monkeypatch, {})
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
                                hook_card=False):
        from marketing import voice as vc

        self.renders += 1
        self.video_calls.append({"hook_card": hook_card, "kinds": [s.kind for s in specs]})
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
    assert world.news.logo_calls == ["LOW", "SBUX"]
    assert sorted(world.logo_uploads) == sorted(lg["url"].split("/marketing-media/", 1)[1] for lg in out["logos"])
    assert all(lg["url"] and lg["sha256"] for lg in out["logos"])
    assert sorted(u for u in world.downloads if "/logos/" in u) == sorted(lg["url"] for lg in out["logos"])
    # the video: the opening card over the hook, one card per line, the disclaimer last
    (video_call,) = world.video_calls
    assert video_call["hook_card"] is True and video_call["kinds"] == ["opening", "text", "text", "text",
                                                                      "text", "disclaimer"]
    (video,) = _ready(world, "video")
    allowed_video = (set(tos.opening_strings(out["opening_card"], out["logos"]))
                     | {c[k] for c in out["cards"] for k in ("title", "body")} | {out["disclaimer_card"]})
    drawn_video = set(video["metadata"]["onscreen_text"])
    assert out["disclaimer_card"] in drawn_video and out["opening_card"]["headline"] in drawn_video
    assert drawn_video - allowed_video <= set(mrs.VIDEO_BRAND_TEXT)
    # the post image: its spec's strings and the footer — never the alt text
    card = _the_card(world)
    drawn_image = set(card["metadata"]["onscreen_text"])
    assert out["image_footer"] in drawn_image
    allowed_image = set(tos.image_strings(out["image_spec"], out["logos"])) | {out["image_footer"]}
    assert drawn_image <= allowed_image
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

    async def candidates(self, series, *, run_date, exclude, limit, deadline):
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
        return (R.CongressCount(series=series, company=co(symbol="CTSO", name="Contoso"), month="2026-11", members=4,
                                fetched_on=day), day, "C", "spotlight", ["congress_count"])
    if series == "company_stakes":      # the first stakes Tuesday, after the 13F season
        day = date(2026, 12, 29)
        return (R.CompanyStake(series=series, stake_id="0a1b2c3d-4e5f-4a6b-8c7d-0000000e2e01",
                               investor=co(symbol="FBKM", name="Fabrikam"), investee_name="Relecloud", investee=None,
                               kind="private", value_usd=640_000_000.0, value_basis="invested", ownership_pct=None,
                               as_of=date(2026, 10, 15), verified_on=date(2026, 12, 15),
                               source_title="Relecloud Form S-1", background=None, listed_since=None,
                               local_listing=None, is_new=True), day, "F", "pair", ["company_stakes"])
    if series == "earnings":            # the first 2b-3 Thursday (the January season)
        day = date(2027, 1, 14)
        return (R.EarningsReport(series=series, company=co(symbol="NWTR", name="Northwind Traders"),
                                 report_date=date(2027, 1, 12), period_end=date(2026, 12, 31), eps_actual=-0.05,
                                 eps_estimate=-0.12, revenue_actual=551_900_000.0, revenue_estimate=543_600_000.0),
                day, "F", "rows", ["earnings"])
    # theme_explainer: a Thursday out of the earnings season — Money Map comes up empty first
    day = date(2026, 11, 26)
    rows = (("CTSO", "Contoso", "Robotics systems", 0.62), ("FBKM", "Fabrikam", "Automation software", 0.48),
            ("NWTR", "Northwind Traders", "Warehouse systems", 0.71), ("TSPN", "Tailspin Toys", "Drones", 0.39),
            ("ADVW", "Adventure Works", "Field services", 0.55), ("PRSW", "Proseware", "Sensors", None),
            ("WDGB", "Woodgrove Bank", None, None), ("LTWR", "Litware", "Logistics software", 0.66))
    members = tuple(R.ThemeMember(company=co(symbol=sym, name=name), top_segment=seg, top_segment_share=share,
                                  fiscal_year="2025" if seg else None) for sym, name, seg, share in rows)
    return (R.ThemeExplainer(series=series, slug="warehouse-robots", title="Warehouse robots", members=members,
                             tickers_as_of=date(2026, 11, 16)), day, "F", "grid", ["money_map", "theme_explainer"])


@pytest.mark.parametrize("series", ["congress_count", "company_stakes", "earnings", "theme_explainer"])
def test_a_2b_day_goes_from_the_build_to_posts_held_for_review_through_the_real_app(monkeypatch, series):
    pytest.importorskip("PIL")
    from app.services.marketing import news_templates, selection
    from app.services.marketing import template_onscreen as tos

    record, day, klass, layout, asked = _2b_day(series)
    assert series in selection.plan_for(day).chain                       # the real calendar reaches it
    world = TemplateWorld(monkeypatch, news=_StubNews2b(series, record), run_date=day, news_series=_EVERY_SERIES)
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
                     | {c[k] for c in out["cards"] for k in ("title", "body")} | {out["disclaimer_card"]})
    drawn_video = set(video["metadata"]["onscreen_text"])
    assert out["disclaimer_card"] in drawn_video and out["opening_card"]["headline"] in drawn_video
    assert drawn_video - allowed_video <= set(mrs.VIDEO_BRAND_TEXT)
    # the post image: drawn in the series' own layout, its spec's strings and the footer only
    assert out["image_spec"]["layout"] == layout
    card = _the_card(world)
    drawn_image = set(card["metadata"]["onscreen_text"])
    assert out["image_footer"] in drawn_image
    assert drawn_image <= set(tos.image_strings(out["image_spec"], out["logos"])) | {out["image_footer"]}
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
        assert out["image_spec"]["figure"] == "4" and "disclosed" in out["hook"]
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

