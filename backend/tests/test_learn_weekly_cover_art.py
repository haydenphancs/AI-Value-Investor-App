"""Cover art for Wiser weekly pieces (`app/services/learn_weekly/cover_art.py`, owner 2026-11-01).

Hermetic: the text and image models are a fake client, Storage is an in-memory fake bucket, and the
"generated" image is a PNG Pillow draws here. What is pinned:

  * the pure parts — weekly-only paths (never a catalog plate, never a traversal), the public URL
    shape, the subject request (the catalog's --auto prompt + the key points, a nonce per attempt),
    the subject checks (length, the shared deny-list, Battles = two, the piece's own company names
    and case-sensitive tickers), the deterministic treatment and the catalog prompt;
  * the step — one subject call and ONE image call, hero 1206×678 + card 640×360 JPEGs uploaded to
    the PUBLIC bucket with upsert off / image/jpeg / cache-control 300; a rebuild of the same slug
    reuses both plates with NO model call; a half pair is removed and re-made; a conflicting upload
    is reused only when its size is equal;
  * `cover_art_for` never raises: off → nothing called; a refusal, a storage error, garbage bytes, a
    bug or a timeout → artless with a WARNING naming the piece, the slug and the error type.
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import unittest.mock as um
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.config import settings
from app.integrations.gemini import GeminiImageRefusedError, GeminiImageUnavailableError
from app.services import money_moves_art_style as style
from app.services.learn_weekly import contracts as C
from app.services.learn_weekly import cover_art as CA

SLUG = "how-chips-became-the-bottleneck-2026-11-06"
HERO = f"https://x.supabase.co/storage/v1/object/public/money-moves-images/articles/{SLUG}.hero.jpg"
CARD = f"https://x.supabase.co/storage/v1/object/public/money-moves-images/articles/{SLUG}.card.jpg"
GOOD = ("a single bare silicon wafer standing upright on its edge, its grid of identical square dies "
        "catching the light, completely blank with no printing or markings anywhere")
TWO = ("two identical unbranded graphics accelerator boards lying flat side by side, their heatsinks and "
       "edge connectors readable, both completely blank and unmarked")


def draft(**over: Any) -> Dict[str, Any]:
    d = {"slug": SLUG, "title": "How Chips Became the Bottleneck",
         "subtitle": "Why a handful of fabs decide who builds AI.", "category": "blueprints",
         "keyHighlights": [{"icon": "cpu.fill", "title": "Scarce capacity",
                            "description": "Few plants can make the most advanced chips."},
                           {"icon": "bolt.fill", "title": "Power", "description": "Grid access matters."},
                           {"icon": "building.2.fill", "title": "Capex", "description": "Plants cost billions."}],
         "sections": [{"title": "S", "content": [{"type": "paragraph", "text": "Body."}]}]}
    d.update(over)
    return d


def png(w: int = 1376, h: int = 768) -> bytes:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (w, h), (30, 50, 80))
    ImageDraw.Draw(im).rectangle((w // 3, h // 3, 2 * w // 3, 2 * h // 3), fill=(220, 180, 90))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class FakeGemini:
    def __init__(self, subjects: Optional[List[Any]] = None, image: Any = None) -> None:
        self.subjects = list(subjects if subjects is not None else [GOOD])
        self.image = png() if image is None else image
        self.text_calls: List[Dict[str, Any]] = []
        self.image_calls: List[Dict[str, Any]] = []
        self.hang = False

    async def generate_text(self, prompt, **kw):
        self.text_calls.append({"prompt": prompt, **kw})
        if self.hang:
            await asyncio.sleep(30)
        item = self.subjects.pop(0) if len(self.subjects) > 1 else self.subjects[0]
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, dict):
            return item
        return {"text": item, "finish_reason": "STOP", "tokens_used": 12}

    async def generate_image(self, prompt, **kw):
        self.image_calls.append({"prompt": prompt, **kw})
        if isinstance(self.image, BaseException):
            raise self.image
        return self.image


class Conflict(Exception):
    status = 409
    code = "Duplicate"


class FakeStore:
    """Supabase Storage as `cover_art` uses it: from_(bucket).list / upload / remove."""

    def __init__(self) -> None:
        self.objects: Dict[tuple, tuple] = {}         # (bucket, path) -> (bytes, content type)
        self.uploads: List[tuple] = []
        self.removed: List[tuple] = []
        self.lists: List[tuple] = []
        self.fail_list: Optional[Exception] = None
        self.fail_upload: Optional[Exception] = None
        self.before_upload = None                      # callable(path) — a concurrent writer
        self.storage = self

    def from_(self, bucket):
        return _Bucket(self, bucket)


class _Bucket:
    def __init__(self, store: FakeStore, bucket: str) -> None:
        self.s, self.b = store, bucket

    def list(self, prefix, opts):
        self.s.lists.append((self.b, prefix, dict(opts)))
        if self.s.fail_list is not None:
            raise self.s.fail_list
        out = []
        for (bucket, path), (data, ctype) in self.s.objects.items():
            folder, _, name = path.rpartition("/")
            if bucket == self.b and folder == prefix and opts.get("search", "") in name:
                out.append({"name": name, "metadata": {"size": len(data), "mimetype": ctype}})
        return out

    def upload(self, path, data, opts):
        if self.s.before_upload is not None:
            self.s.before_upload(path)
        self.s.uploads.append((self.b, path, dict(opts), len(data)))
        if self.s.fail_upload is not None:
            raise self.s.fail_upload
        if (self.b, path) in self.s.objects and opts.get("upsert") == "false":
            raise Conflict("The resource already exists")
        self.s.objects[(self.b, path)] = (bytes(data), opts.get("content-type"))
        return {"Key": path}

    def remove(self, paths):
        self.s.removed.append((self.b, list(paths)))
        for p in paths:
            self.s.objects.pop((self.b, p), None)
        return [{"name": p} for p in paths]


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(settings, "LEARN_ART_MODEL", "image-model-x")
    monkeypatch.setattr(settings, "LEARN_ART_SUBJECT_MODEL", "text-model-y")
    monkeypatch.setattr(settings, "LEARN_ART_ENABLED", True)
    monkeypatch.setattr(settings, "LEARN_ART_TIMEOUT_SECONDS", 120)


def run(coro):
    return asyncio.run(coro)


# ── pure parts ────────────────────────────────────────────────────────────────────────────────


def test_art_paths_are_the_catalogs_layout_for_a_weekly_slug_only():
    assert CA.art_paths(SLUG) == {"hero": f"articles/{SLUG}.hero.jpg", "card": f"articles/{SLUG}.card.jpg"}
    for bad in ("the-fall-of-enron", "../etc-2026-11-06", "Upper-Case-2026-11-06", "x-2026-11-06/..",
                "a b-2026-11-06", "", None, 7, "x" * 170 + "-2026-11-06"):
        with pytest.raises(ValueError):
            CA.art_paths(bad)


def test_public_url_is_the_unsigned_public_form_of_the_image_bucket():
    assert CA.public_url(f"articles/{SLUG}.hero.jpg") == HERO
    assert CA.BUCKET == "money-moves-images" and CA.BUCKET != "money-moves-media"
    assert CA.CONTENT_TYPE == "image/jpeg" and CA.CACHE_CONTROL == "300"


def test_the_bucket_matches_the_seeder_and_is_never_signable():
    from app.services import learn_audio_urls

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import seed_money_moves

    assert CA.BUCKET == seed_money_moves.IMAGE_BUCKET and CA.PREFIX == seed_money_moves.IMAGE_PREFIX
    assert CA.BUCKET not in learn_audio_urls._SIGNABLE_BUCKETS


def test_subject_request_is_the_catalog_prompt_plus_the_key_points_and_a_nonce():
    a = CA.subject_request(draft(), attempt=1)
    b = CA.subject_request(draft(), attempt=2)
    assert a != b and a.startswith(f"REQUEST cover-art {SLUG} attempt 1")
    assert style.subject_prompt("How Chips Became the Bottleneck",
                                "Why a handful of fabs decide who builds AI.", "blueprints") in a
    assert "- Scarce capacity: Few plants can make the most advanced chips." in a
    assert "KEY POINTS OF THE ARTICLE" in a
    # battles get the two-identical-objects shape; junk fields never crash
    assert style._SHAPE["battles"] in CA.subject_request(draft(category="battles"), attempt=1)
    junk = CA.subject_request({"slug": SLUG, "title": 5, "keyHighlights": "x", "category": "?"}, attempt=1)
    assert style._SHAPE["blueprints"] in junk and "KEY POINTS" not in junk


def test_subject_ban_terms_are_whole_names_and_upper_case_tickers():
    terms = CA.subject_ban_terms([C.Subject(name="NVIDIA CORP", cik="0001045810", ticker="NVDA"),
                                  {"name": "Taiwan Semiconductor Manufacturing Co Ltd", "ticker": "TSM"},
                                  {"name": "Advanced Micro Devices, Inc.", "ticker": "AMD"},
                                  {"name": "ON Semiconductor Corp", "ticker": "ON"}])
    assert "nvidia" in terms and "NVDA" in terms and "taiwan semiconductor manufacturing" in terms
    assert "advanced micro devices" in terms and "advanced" not in terms and "ON" in terms


@pytest.mark.parametrize("name, ticker", [
    ("Agilent Technologies Inc", "A"), ("Citigroup Inc", "C"), ("Visa Inc. Class A", "V"),
    ("Ford Motor Co", "F"), ("AT&T Inc", "T"), ("Kellanova", "K"), ("Realty Income Corp", "O"),
])
def test_a_one_letter_ticker_never_joins_the_ban_list_but_the_name_does(name, ticker):
    """A one-letter ticker as a case-sensitive whole word matches the article "A" (or any lone
    capital), so every subject would be refused and the piece would go artless after three paid calls."""
    terms = CA.subject_ban_terms([{"name": name, "ticker": ticker}])
    assert ticker not in terms and terms, terms
    assert all(len(t) >= 2 for t in terms)


def test_agilent_a_subject_opening_with_the_article_a_is_usable_and_its_name_still_refused():
    terms = CA.subject_ban_terms([{"name": "Agilent Technologies Inc", "ticker": "A"}])
    assert terms == ["agilent technologies"]
    lab = ("A single unbranded laboratory pipette standing upright beside a blank glass vial, "
           "completely unmarked.")
    assert CA.subject_problem(lab, "blueprints", terms) is None
    named = ("A single laboratory pipette from Agilent Technologies standing upright beside a blank "
             "glass vial, completely unmarked.")
    assert "agilent technologies" in (CA.subject_problem(named, "blueprints", terms) or "")
    # Through the whole subject step: one call, the first answer is used.
    client = FakeGemini([lab])
    assert run(CA.write_subject(draft(), banned=terms, client=client)) == lab
    assert len(client.text_calls) == 1


def test_a_two_letter_ticker_is_still_banned():
    terms = CA.subject_ban_terms([{"name": "General Electric Co", "ticker": "GE"}])
    assert "GE" in terms
    assert CA.subject_problem("a jet engine fan stamped GE on a plain stand, every blade unmarked "
                              "and the casing blank", "blueprints", terms) is not None


@pytest.mark.parametrize("text, category, banned, ok", [
    (GOOD, "blueprints", ["nvidia", "NVDA"], True),
    ("an unbranded NVIDIA accelerator board lying flat, its heatsink readable and completely blank",
     "blueprints", ["nvidia", "NVDA"], False),
    ("an unbranded accelerator board stamped NVDA lying flat on a plain bench, nothing else on it",
     "blueprints", ["nvidia", "NVDA"], False),
    # a two-letter ticker is matched case-sensitively: the word "on" is never refused
    ("a single bare silicon wafer standing on its edge, its dies catching the light, fully blank",
     "blueprints", ["on semiconductor", "ON"], True),
    ("a single bare silicon wafer, a generic advanced chip die beside it, both completely blank",
     "blueprints", ["advanced micro devices", "AMD"], True),
    ("x" * 41 + " " + "word " * 140, "blueprints", [], False),               # too long
    (GOOD, "battles", [], False),                                            # Battles needs two
    (TWO, "battles", [], True),
    ("two identical drinks cans, one with a person's portrait printed on it, unbranded otherwise",
     "battles", [], False),                                                  # deny-list
])
def test_subject_problem(text, category, banned, ok):
    assert (CA.subject_problem(text, category, banned) is None) is ok


def test_the_image_prompt_is_the_catalog_prompt_on_the_slugs_treatment():
    prompt, treatment = CA.image_prompt(GOOD, "valueTraps", SLUG)
    assert treatment == style.default_treatment(SLUG, "valueTraps")
    assert prompt == style.prompt_for(GOOD, "valueTraps", treatment)
    assert style.PALETTES["valueTraps"] in prompt and "NO text" in prompt
    assert CA.image_prompt(GOOD, "valueTraps", SLUG) == (prompt, treatment)        # deterministic
    assert CA.image_prompt(GOOD, "nonsense", SLUG)[0] == style.prompt_for(
        GOOD, "blueprints", style.default_treatment(SLUG, "blueprints"))
    assert len(CA.prompt_sha1(prompt)) == 12


def test_clean_subject():
    assert CA.clean_subject('  "a coin\n standing on its edge"  ') == "a coin standing on its edge"
    assert CA.clean_subject(None) == ""


# ── the subject call ──────────────────────────────────────────────────────────────────────────


def test_an_unusable_subject_is_asked_again_with_a_new_nonce():
    client = FakeGemini(["a stack of amazon boxes " * 3, GOOD])
    assert run(CA.write_subject(draft(), client=client)) == GOOD
    assert len(client.text_calls) == 2
    assert client.text_calls[0]["prompt"] != client.text_calls[1]["prompt"]
    assert client.text_calls[0]["model_name"] == "text-model-y"
    assert client.text_calls[0]["thinking_budget"] == 0


def test_a_cut_answer_is_never_used():
    client = FakeGemini([{"text": GOOD[:60], "finish_reason": "MAX_TOKENS"}, GOOD])
    assert run(CA.write_subject(draft(), client=client)) == GOOD and len(client.text_calls) == 2


def test_three_unusable_subjects_fail_the_subject(caplog):
    client = FakeGemini(["too short"])
    with pytest.raises(CA.CoverArtSubjectError) as e:
        run(CA.write_subject(draft(), client=client, piece_id="p-1"))
    assert len(client.text_calls) == CA.SUBJECT_ATTEMPTS == 3
    assert "too short" in str(e.value) and SLUG in str(e.value) and "p-1" in str(e.value)


def test_a_failed_subject_call_is_a_typed_error_not_a_retry():
    client = FakeGemini([RuntimeError("boom")])
    with pytest.raises(CA.CoverArtSubjectError):
        run(CA.write_subject(draft(), client=client))
    assert len(client.text_calls) == 1


def test_the_piece_own_company_is_refused_in_a_subject():
    client = FakeGemini(["an unbranded nvidia accelerator board lying flat, completely blank and plain",
                         GOOD])
    subj = [C.Subject(name="NVIDIA CORP", cik="0001045810", ticker="NVDA")]
    assert run(CA.write_subject(draft(), banned=CA.subject_ban_terms(subj), client=client)) == GOOD


# ── the whole step ────────────────────────────────────────────────────────────────────────────


def test_one_subject_one_image_two_public_uploads():
    from PIL import Image

    client, store = FakeGemini(), FakeStore()
    out = run(CA.make_cover_art(draft(), client=client, sb=store, piece_id="p-1"))
    assert out["imageUrl"] == HERO and out["imageCardUrl"] == CARD
    assert len(client.text_calls) == 1 and len(client.image_calls) == 1
    call = client.image_calls[0]
    prompt, treatment = CA.image_prompt(GOOD, "blueprints", SLUG)
    assert call["prompt"] == prompt and call["model_name"] == "image-model-x"
    assert call["aspect_ratio"] == "16:9" and call["image_size"] == "2K"
    assert [u[1] for u in store.uploads] == [f"articles/{SLUG}.hero.jpg", f"articles/{SLUG}.card.jpg"]
    for bucket, _path, opts, _n in store.uploads:
        assert bucket == "money-moves-images"
        assert opts == {"content-type": "image/jpeg", "cache-control": "300", "upsert": "false"}
    for name, (w, h) in style.DERIVATIVES.items():
        data, ctype = store.objects[("money-moves-images", f"articles/{SLUG}.{name}.jpg")]
        assert ctype == "image/jpeg"
        with Image.open(io.BytesIO(data)) as im:
            assert im.format == "JPEG" and im.size == (w, h)
    rec = out["coverArt"]
    assert rec["subject"] == GOOD and rec["treatment"] == treatment and rec["model"] == "image-model-x"
    assert rec["prompt_sha1"] == CA.prompt_sha1(prompt) and rec["reused"] is False
    assert (rec["width"], rec["height"]) == (1376, 768)


def test_a_rebuild_of_the_same_slug_reuses_the_plates_without_any_model_call():
    store = FakeStore()
    first = run(CA.make_cover_art(draft(), client=FakeGemini(), sb=store))
    client = FakeGemini()
    previous = {**first["coverArt"], "imageUrl": first["imageUrl"]}
    again = run(CA.make_cover_art(draft(title="Another Title"), client=client, sb=store, previous=previous))
    assert client.text_calls == [] and client.image_calls == []
    assert again["imageUrl"] == HERO and again["imageCardUrl"] == CARD
    assert again["coverArt"]["reused"] is True and again["coverArt"]["subject"] == GOOD
    assert again["coverArt"]["prompt_sha1"] == first["coverArt"]["prompt_sha1"]
    assert len(store.uploads) == 2                      # nothing uploaded the second time
    # without a matching previous record the reuse is still free, just unannotated
    bare = run(CA.make_cover_art(draft(), client=client, sb=store, previous={"imageUrl": "https://elsewhere"}))
    assert bare["coverArt"]["reused"] is True and "subject" not in bare["coverArt"]


def test_a_half_pair_is_removed_and_made_again_as_one_picture():
    store = FakeStore()
    store.objects[("money-moves-images", f"articles/{SLUG}.hero.jpg")] = (b"old-hero", "image/jpeg")
    client = FakeGemini()
    out = run(CA.make_cover_art(draft(), client=client, sb=store))
    assert store.removed == [("money-moves-images", [f"articles/{SLUG}.hero.jpg"])]
    assert len(client.image_calls) == 1 and out["imageUrl"] == HERO
    assert store.objects[("money-moves-images", f"articles/{SLUG}.hero.jpg")][0] != b"old-hero"


def test_a_concurrent_object_of_the_same_size_is_reused_and_another_is_refused():
    store = FakeStore()
    sizes: Dict[str, int] = {}

    def same_size_writer(path):
        if path.endswith(".hero.jpg") and ("money-moves-images", path) not in store.objects:
            store.objects[("money-moves-images", path)] = (b"\0" * sizes["hero"], "image/jpeg")

    real = style.derive_all_jpeg

    def spy(master):
        out = real(master)
        sizes.update({k: len(v) for k, v in out.items()})
        return out

    store.before_upload = same_size_writer
    with um.patch.object(style, "derive_all_jpeg", spy):
        out = run(CA.make_cover_art(draft(), client=FakeGemini(), sb=store))
    assert out["imageUrl"] == HERO

    other = FakeStore()
    other.before_upload = lambda path: other.objects.setdefault(("money-moves-images", path), (b"x", "image/jpeg"))
    with pytest.raises(CA.CoverArtStorageError):
        run(CA.make_cover_art(draft(), client=FakeGemini(), sb=other))


def test_storage_failures_are_typed():
    store = FakeStore()
    store.fail_list = RuntimeError("storage down")
    with pytest.raises(CA.CoverArtStorageError):
        run(CA.make_cover_art(draft(), client=FakeGemini(), sb=store))
    store = FakeStore()
    store.fail_upload = RuntimeError("500")
    with pytest.raises(CA.CoverArtStorageError):
        run(CA.make_cover_art(draft(), client=FakeGemini(), sb=store))


@pytest.mark.parametrize("image", [GeminiImageRefusedError("SAFETY"), GeminiImageUnavailableError("x"),
                                   RuntimeError("bug"), b"not an image at all", b""])
def test_image_failures_are_typed(image):
    with pytest.raises(CA.CoverArtImageError):
        run(CA.make_cover_art(draft(), client=FakeGemini(image=image), sb=FakeStore()))


# ── cover_art_for: the build's door, which never raises ───────────────────────────────────────


def test_off_calls_nothing(monkeypatch):
    monkeypatch.setattr(settings, "LEARN_ART_ENABLED", False)
    client, store = FakeGemini(), FakeStore()
    assert run(CA.cover_art_for(draft(), client=client, sb=store)) == (None, {"status": "off"})
    assert client.text_calls == [] and store.lists == []


def test_success_returns_only_the_two_image_fields_and_the_record():
    fields, rec = run(CA.cover_art_for(draft(), client=FakeGemini(), sb=FakeStore(), piece_id="p-1"))
    assert fields == {"imageUrl": HERO, "imageCardUrl": CARD}
    assert rec["status"] == "ok" and rec["imageUrl"] == HERO and rec["subject"] == GOOD


@pytest.mark.parametrize("make, needle", [
    (lambda: (FakeGemini(image=GeminiImageRefusedError("SAFETY")), FakeStore()), "CoverArtImageError"),
    (lambda: (FakeGemini(["no"]), FakeStore()), "CoverArtSubjectError"),
])
def test_a_failure_is_artless_and_logged_with_its_ids(make, needle, caplog):
    client, store = make()
    with caplog.at_level(logging.WARNING, logger="app.services.learn_weekly.cover_art"):
        fields, rec = run(CA.cover_art_for(draft(), client=client, sb=store, piece_id="p-9"))
    assert fields is None and rec["status"] == "failed" and needle in rec["error"]
    line = [r.getMessage() for r in caplog.records if "without cover art" in r.getMessage()]
    assert line and "p-9" in line[0] and SLUG in line[0] and needle in line[0]


def test_a_timeout_is_artless(monkeypatch, caplog):
    monkeypatch.setattr(settings, "LEARN_ART_TIMEOUT_SECONDS", 0.05)
    client = FakeGemini()
    client.hang = True
    with caplog.at_level(logging.WARNING, logger="app.services.learn_weekly.cover_art"):
        fields, rec = run(CA.cover_art_for(draft(), client=client, sb=FakeStore(), piece_id="p-2"))
    assert fields is None and rec == {"status": "failed", "error": "timed out after 0.05s"}
    assert any("p-2" in r.getMessage() and "TimeoutError" in r.getMessage() for r in caplog.records)


def test_a_bug_or_a_bad_slug_is_artless_with_the_stack(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.learn_weekly.cover_art"):
        fields, rec = run(CA.cover_art_for(draft(slug="the-fall-of-enron"), client=FakeGemini(),
                                           sb=FakeStore(), piece_id="p-3"))
    assert fields is None and rec["status"] == "failed" and "ValueError" in rec["error"]
    assert any(r.exc_info for r in caplog.records if "p-3" in r.getMessage())


def test_cancellation_propagates():
    async def go():
        client = FakeGemini()
        client.hang = True
        task = asyncio.ensure_future(CA.cover_art_for(draft(), client=client, sb=FakeStore()))
        await asyncio.sleep(0.01)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        run(go())


# ── ready-made plates (W44, owner 2026-11-01 "W44 now") ───────────────────────────────────────


def plates(color=(30, 50, 80)) -> Dict[str, bytes]:
    from PIL import Image

    out = {}
    for name, (w, h) in style.DERIVATIVES.items():
        buf = io.BytesIO()
        Image.new("RGB", (w, h), color).save(buf, "JPEG", quality=84)
        out[name] = buf.getvalue()
    return out


def test_store_plates_uploads_the_pair_once_and_reuses_it_on_a_rerun():
    store, p = FakeStore(), plates()
    out = run(CA.store_plates(SLUG, p, sb=store, piece_id="p-44"))
    assert out["imageUrl"] == HERO and out["imageCardUrl"] == CARD
    assert out["coverArt"]["status"] == "ok" and out["coverArt"]["reused"] is False
    assert {(u[1], u[2]["content-type"], u[2]["upsert"]) for u in store.uploads} == {
        (f"articles/{SLUG}.hero.jpg", "image/jpeg", "false"), (f"articles/{SLUG}.card.jpg", "image/jpeg", "false")}
    assert store.objects[("money-moves-images", f"articles/{SLUG}.hero.jpg")][0] == p["hero"]
    again = run(CA.store_plates(SLUG, p, sb=store))
    assert again["coverArt"]["reused"] is True and len(store.uploads) == 2, "a rerun uploads nothing"


def test_store_plates_refuses_another_picture_and_removes_a_half_pair():
    store = FakeStore()
    store.objects[("money-moves-images", f"articles/{SLUG}.hero.jpg")] = (b"x" * 10, "image/jpeg")
    store.objects[("money-moves-images", f"articles/{SLUG}.card.jpg")] = (b"y" * 10, "image/jpeg")
    with pytest.raises(CA.CoverArtStorageError, match="another picture"):
        run(CA.store_plates(SLUG, plates(), sb=store))
    assert store.uploads == [] and store.removed == [], "left in place"
    half = FakeStore()
    half.objects[("money-moves-images", f"articles/{SLUG}.card.jpg")] = (b"old", "image/jpeg")
    run(CA.store_plates(SLUG, plates(), sb=half))
    assert half.removed == [("money-moves-images", [f"articles/{SLUG}.card.jpg"])]
    assert half.objects[("money-moves-images", f"articles/{SLUG}.card.jpg")][0] == plates()["card"]


@pytest.mark.parametrize("make, needle", [
    (lambda p: {"hero": p["hero"]}, "want exactly"),
    (lambda p: {"hero": p["card"], "card": p["card"]}, "want 1206x678"),
    (lambda p: {"hero": p["hero"][:400], "card": p["card"]}, "not a readable image"),
    (lambda p: {"hero": png(1206, 678), "card": p["card"]}, "PNG is not a JPEG"),
    (lambda p: {"hero": b"", "card": p["card"]}, "no bytes"),
    (lambda p: {"hero": b"\xff" * (CA.MAX_OBJECT_BYTES + 1), "card": p["card"]}, "cap"),
])
def test_unusable_plates_are_refused_before_any_storage_call(make, needle):
    store = FakeStore()
    with pytest.raises(CA.CoverArtImageError, match=needle):
        run(CA.store_plates(SLUG, make(plates()), sb=store))
    assert store.lists == [] and store.uploads == []


def test_store_plates_takes_only_a_weekly_slug():
    with pytest.raises(ValueError, match="not a weekly slug"):
        run(CA.store_plates("coca-cola-vs-pepsico", plates(), sb=FakeStore()))
