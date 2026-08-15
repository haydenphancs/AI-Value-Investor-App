"""The forced-alignment scripts must not fetch a PRIVATE bucket over the public URL.

The defect this pins shipped and went unnoticed for months, because it fails silently and in
a way that reads as a different problem:

`_forced_align.download_public()` built `{SUPABASE_URL}/storage/v1/object/public/{bucket}/...`
and plain-GET it. Migration 128 then set `journey-media`, `money-moves-media` AND `book-media`
to `public = false` and dropped their `*_public_read` policies. Every such fetch has 404'd
ever since — and the caller warns, returns False, and the aligner prints "skipped (no audio)".
So "the download is broken" is indistinguishable from "this item was never narrated", which is
why nobody looked. The fix is a service-role download (`download_object`); service_role
bypasses RLS and is the same key `seed_money_moves.py` uploads with, so the aligner reads back
exactly the bytes it published.

The bucket list is derived from migration 128 rather than hardcoded, so a FOURTH aligner — or
a fourth bucket added to the flip — is covered without editing this file.

⚠️ `money-moves-images` must never be dragged into this. Artwork is public and free on every
tier by design (migration 137); signing it would put a paywall in front of free content.

Category 1 (pure) — no network, no Supabase.
"""
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND / "scripts"
MIGRATION_128 = BACKEND / "database/migrations/128_learn_media_buckets_private.sql"
FORCED_ALIGN = SCRIPTS / "_forced_align.py"
LEARN_AUDIO_URLS = BACKEND / "app/services/learn_audio_urls.py"

IMAGE_BUCKET = "money-moves-images"


def _sql_body(path: Path) -> str:
    """128's header prose names buckets it does NOT flip, and names the public URL shape in a
    verification note. Either would satisfy a naive substring search."""
    return "\n".join(l for l in path.read_text().splitlines()
                     if not l.lstrip().startswith("--"))


def _private_buckets() -> set[str]:
    """The buckets migration 128 actually flipped, read out of the UPDATE statement."""
    body = _sql_body(MIGRATION_128)
    m = re.search(r"UPDATE\s+storage\.buckets.*?WHERE\s+id\s+IN\s*\(([^)]*)\)", body,
                  flags=re.S | re.I)
    assert m, "could not find 128's bucket flip — has the migration been rewritten?"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def _aligners() -> list[Path]:
    return sorted(p for p in SCRIPTS.glob("align_*.py"))


def _py_body(path: Path) -> str:
    """Strip docstrings and comments: this file's whole point is that the PROSE was wrong for
    months while claiming the buckets were public, so prose must never satisfy an assertion."""
    src = re.sub(r'"""(?:.|\n)*?"""', "", path.read_text())
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def test_migration_128_made_the_learn_media_buckets_private():
    private = _private_buckets()
    assert {"journey-media", "money-moves-media", "book-media"} <= private
    assert IMAGE_BUCKET not in private, (
        f"{IMAGE_BUCKET} is in 128's flip list — artwork is FREE on every tier and must stay "
        "public; signing it hides free content behind the paywall")


def test_aligners_never_fetch_a_private_bucket_over_the_public_path():
    """No aligner may reach a 128-private bucket through `/object/public/`."""
    private = _private_buckets()
    body = _py_body(FORCED_ALIGN)
    assert "/object/public/" not in body, (
        "_forced_align still builds a public-object URL; every 128 bucket 404s on it")
    assert "download_public" not in body, (
        "download_public is back — the name asserts a fact that migration 128 falsified")

    offenders = []
    for script in _aligners():
        s = _py_body(script)
        if "/object/public/" not in s:
            continue
        for bucket in private:
            if bucket in s:
                offenders.append(f"{script.name} -> {bucket}")
    assert not offenders, (
        "aligner fetches a private bucket over the public path:\n  " + "\n  ".join(offenders))


def test_every_aligner_that_downloads_uses_the_service_role_helper():
    users = [p for p in _aligners() if "download_" in _py_body(p)]
    assert users, "no aligner downloads anything — has the helper been renamed?"
    for script in users:
        body = _py_body(script)
        assert "download_object" in body, (
            f"{script.name} downloads via something other than the service-role helper")


def test_the_downloader_uses_the_service_role_client():
    """`get_supabase()` is the service-role client (SUPABASE_SERVICE_ROLE_KEY, per
    app/config.py), which is what bypasses RLS on a private bucket."""
    body = _py_body(FORCED_ALIGN)
    assert "def download_object" in body
    fn = body.split("def download_object", 1)[1]
    assert "get_supabase" in fn, "the downloader does not use the service-role client"
    assert ".storage.from_(" in fn and ".download(" in fn, "not an SDK Storage download"
    # Lazy import: app.database pulls app.config.Settings, which needs a populated .env.
    # Aligning a clip that is ALREADY on disk must keep working without one.
    assert "from app.database import get_supabase" in fn, (
        "the app.database import must be inside download_object — a module-level import makes "
        "a fully-local alignment require backend/.env")


def test_no_script_or_service_still_calls_the_learn_media_buckets_public():
    """The prose is the bug's accomplice. `learn_audio_urls` and both aligners each carried a
    docstring stating these buckets are public long after 128 applied."""
    private = _private_buckets()
    pattern = re.compile(r"public[^.\n]{0,40}bucket|bucket[^.\n]{0,40}(?:are|is) (?:all )?public",
                         re.I)
    offenders = []
    for path in [*_aligners(), FORCED_ALIGN, LEARN_AUDIO_URLS]:
        text = path.read_text()
        for m in pattern.finditer(text):
            window = text[max(0, m.start() - 200): m.end() + 200]
            if any(b in window for b in private):
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line}: {m.group(0).strip()!r}")
    assert not offenders, (
        "documentation still describes a 128-private bucket as public:\n  "
        + "\n  ".join(offenders))


# --------------------------------------------------------------------------
# Anti-vacuity
# --------------------------------------------------------------------------
def test_the_scans_are_not_vacuous():
    assert MIGRATION_128.exists() and FORCED_ALIGN.exists() and LEARN_AUDIO_URLS.exists()
    assert len(_aligners()) >= 2, f"only {len(_aligners())} aligners found — glob drifted"
    assert len(_private_buckets()) == 3, f"parsed {_private_buckets()} from 128"

    # _sql_body must really strip: 128's header discusses the public URL shape in its
    # verification note, which the "/object/public/" search would otherwise trip on.
    raw = MIGRATION_128.read_text()
    assert "/object/public/" in raw, "128's verification note is gone"
    assert "/object/public/" not in _sql_body(MIGRATION_128), "_sql_body failed to strip"

    # _py_body must really strip: the corrected docstrings still NAME the buckets and the
    # public path while explaining that both are historical.
    assert "128" in FORCED_ALIGN.read_text(), "the reasoning docstring is gone"
    probe = '"""x public bucket x"""\n# public bucket\nCODE = 1\n'
    assert "public bucket" not in _py_body_str(probe)

    # And the prose detector must fire on the exact strings that shipped.
    pattern = re.compile(r"public[^.\n]{0,40}bucket|bucket[^.\n]{0,40}(?:are|is) (?:all )?public",
                         re.I)
    for shipped in (
        "Missing audio is downloaded from the public money-moves-media bucket first (free).",
        "`money-moves-media` are all PUBLIC buckets (migrations 068 / 061 / 065)",
        "Also provides a tiny public-bucket downloader so the aligners can pull",
    ):
        assert pattern.search(shipped), f"detector would not have caught: {shipped!r}"


def _py_body_str(src: str) -> str:
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
