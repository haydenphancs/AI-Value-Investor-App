"""A re-seed must never blank a live Money Moves cover.

Found by a dry run on 2026-11-05, one step before a real seed. `storage3` 0.12.1 builds `SyncBucket`
straight from the server's JSON, so when Supabase added `versioning_status` to that body every 200
from `get_bucket` became a TypeError. The seed read that as "bucket not found", took its "audio and
text only" path, and `upload_art` returned {} for all 26 articles. `upsert` writes what it is given:
the real run would have set `image_url` to NULL and dropped `imageUrl` / `imageCardUrl` from `content`
on every catalog row.

Two independent guards, each tested alone: the probe no longer depends on parsing a bucket, and no
failure path can replace a stored URL with nothing.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from storage3._sync.bucket import SyncBucket

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND / "scripts"))

seed = pytest.importorskip("seed_money_moves")

HERO = "https://x.supabase.co/storage/v1/object/public/money-moves-images/articles/a.hero.jpg"
CARD = "https://x.supabase.co/storage/v1/object/public/money-moves-images/articles/a.card.jpg"
#: The shape the live server answers with on 2026-11-05: one field more than storage3 0.12.1 knows.
BUCKET_BODY = {"id": "money-moves-images", "name": "money-moves-images", "owner": "", "public": True,
               "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
               "file_size_limit": None, "allowed_mime_types": None, "type": "STANDARD",
               "versioning_status": "disabled"}


class FakeBucketApi:
    def __init__(self, parent):
        self.parent = parent

    def list(self, path, options=None):
        self.parent.listed.append(path)
        return list(self.parent.objects)

    def get_public_url(self, path):
        return f"https://x.supabase.co/storage/v1/object/public/{path}"

    def upload(self, path, body, options=None):
        self.parent.uploaded.append(path)


class FakeStorage:
    def __init__(self, *, objects=(), get_bucket=None):
        self.objects = list(objects)
        self._get_bucket = get_bucket
        self.get_bucket_calls = 0
        self.listed = []
        self.uploaded = []

    def from_(self, _bucket):
        return FakeBucketApi(self)

    def get_bucket(self, bucket_id):
        self.get_bucket_calls += 1
        return self._get_bucket(bucket_id)


class FakeTable:
    def __init__(self, parent):
        self.parent = parent
        self._pending = None

    def select(self, _columns):
        return self

    def upsert(self, rows, on_conflict=None):
        self.parent.upserted.extend(rows)
        return self

    def execute(self):
        return SimpleNamespace(data=self.parent.rows)


class FakeSupabase:
    def __init__(self, storage, rows=()):
        self.storage = storage
        self.rows = list(rows)
        self.upserted = []

    def table(self, _name):
        return FakeTable(self)


def _parse_like_the_sdk(_bucket_id):
    return SyncBucket(**BUCKET_BODY)


def _missing(_bucket_id):
    raise RuntimeError("Bucket not found")


@pytest.fixture(autouse=True)
def _clean_registries(monkeypatch):
    """Module-level caches: a leaked entry would make these pass alone and fail together."""
    for reg in (seed._EXISTING_ART, seed._EXISTING_PUBLISHED_AT, seed._EXISTING_AUDIO, seed._EXISTING_IMAGES):
        reg.clear()
    monkeypatch.setattr(seed, "_IMAGE_BUCKET_OK", False)
    yield
    for reg in (seed._EXISTING_ART, seed._EXISTING_PUBLISHED_AT, seed._EXISTING_AUDIO, seed._EXISTING_IMAGES):
        reg.clear()


# --- the probe ---------------------------------------------------------------------------------

def test_the_sdk_really_cannot_parse_the_live_bucket_body():
    """The premise. If a later storage3 accepts the field this fails, and the note in
    `_image_bucket_exists` can go."""
    with pytest.raises(TypeError, match="versioning_status"):
        _parse_like_the_sdk("money-moves-images")


def test_a_bucket_with_objects_is_confirmed_without_parsing_anything():
    storage = FakeStorage(objects=[{"name": "a.hero.jpg"}], get_bucket=_parse_like_the_sdk)
    assert seed._image_bucket_exists(SimpleNamespace(storage=storage)) is True
    assert storage.get_bucket_calls == 0


def test_an_empty_listing_with_an_unparseable_200_still_means_the_bucket_exists():
    storage = FakeStorage(objects=[], get_bucket=_parse_like_the_sdk)
    assert seed._image_bucket_exists(SimpleNamespace(storage=storage)) is True
    assert storage.get_bucket_calls == 1


def test_a_truly_missing_bucket_is_not_reported_as_present():
    """`.list()` on a missing bucket returns [] too, so the SDK's error must reach the caller."""
    storage = FakeStorage(objects=[], get_bucket=_missing)
    with pytest.raises(RuntimeError, match="not found"):
        seed._image_bucket_exists(SimpleNamespace(storage=storage))


# --- stored URLs -------------------------------------------------------------------------------

@pytest.mark.parametrize("row, want", [
    ({"image_url": HERO, "content": {"imageCardUrl": CARD}}, {"imageUrl": HERO, "imageCardUrl": CARD}),
    ({"image_url": None, "content": {"imageUrl": HERO}}, {"imageUrl": HERO}),
    ({"image_url": "", "content": {"imageUrl": "", "imageCardUrl": ""}}, {}),
    ({"image_url": 7, "content": {"imageCardUrl": ["x"]}}, {}),
    ({"image_url": None, "content": None}, {}),
    ({"image_url": None, "content": "not a dict"}, {}),
    ({}, {}),
])
def test_only_usable_urls_are_remembered(row, want):
    assert seed._stored_art(row) == want


def _article():
    return {"slug": "a", "title": "A", "category": "blueprints", "publishedDaysAgo": 0}


def _sb():
    return SimpleNamespace(storage=FakeStorage())


def test_a_failed_art_step_keeps_the_stored_cover(monkeypatch):
    """THE REGRESSION, at the row level."""
    seed._EXISTING_ART["a"] = {"imageUrl": HERO, "imageCardUrl": CARD}
    monkeypatch.setattr(seed, "upload_art", lambda _sb, _slug: {})
    row = seed.build_row(_sb(), _article(), 0)
    assert row["image_url"] == HERO
    assert row["content"]["imageUrl"] == HERO and row["content"]["imageCardUrl"] == CARD


def test_new_art_wins_field_by_field(monkeypatch):
    seed._EXISTING_ART["a"] = {"imageUrl": HERO, "imageCardUrl": CARD}
    monkeypatch.setattr(seed, "upload_art", lambda _sb, _slug: {"imageUrl": HERO + "?new"})
    row = seed.build_row(_sb(), _article(), 0)
    assert row["image_url"] == HERO + "?new"
    assert row["content"]["imageCardUrl"] == CARD


def test_an_article_with_no_stored_art_stays_empty(monkeypatch):
    monkeypatch.setattr(seed, "upload_art", lambda _sb, _slug: {})
    row = seed.build_row(_sb(), _article(), 0)
    assert row["image_url"] is None and "imageUrl" not in row["content"]


# --- end to end --------------------------------------------------------------------------------

def _run_main(monkeypatch, tmp_path, storage, stored_rows):
    bundle = tmp_path / "money_moves.json"
    bundle.write_text(json.dumps({"articles": [_article()]}))
    sb = FakeSupabase(storage, stored_rows)
    monkeypatch.setattr(seed, "get_supabase", lambda: sb)
    monkeypatch.setattr(seed, "JSON_PATH", bundle)
    monkeypatch.setattr(seed, "AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(seed, "ART_DIR", tmp_path / "art")
    monkeypatch.setattr(seed, "upload_placeholder_art", lambda _sb: {})
    monkeypatch.setattr(seed, "DRY", False)
    seed.main()
    return sb.upserted


def test_main_with_an_unconfirmed_bucket_never_blanks_a_cover(monkeypatch, tmp_path):
    """The whole path of the 2026-11-05 near miss, with the bucket genuinely unconfirmed."""
    rows = [{"slug": "a", "published_at": "2026-08-22T00:00:00+00:00", "image_url": HERO,
             "content": {"imageUrl": HERO, "imageCardUrl": CARD}}]
    upserted = _run_main(monkeypatch, tmp_path, FakeStorage(objects=[], get_bucket=_missing), rows)
    assert seed._IMAGE_BUCKET_OK is False
    assert upserted[0]["image_url"] == HERO
    assert upserted[0]["content"]["imageCardUrl"] == CARD


def test_main_with_the_live_sdk_failure_confirms_the_bucket(monkeypatch, tmp_path):
    rows = [{"slug": "a", "published_at": "2026-08-22T00:00:00+00:00", "image_url": HERO,
             "content": {"imageUrl": HERO, "imageCardUrl": CARD}}]
    storage = FakeStorage(objects=[{"name": "a.hero.jpg"}], get_bucket=_parse_like_the_sdk)
    upserted = _run_main(monkeypatch, tmp_path, storage, rows)
    assert seed._IMAGE_BUCKET_OK is True
    assert upserted[0]["image_url"] == HERO
