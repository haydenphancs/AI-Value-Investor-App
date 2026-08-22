"""The authored Money Moves catalog must be fully produced and internally consistent.

WHY THIS FILE EXISTS. Three articles — amd-vs-intel-the-cpu-wars, the-fall-of-sears,
nvidias-ai-dominance — were authored into money_moves.json and then sat UNPUBLISHED for six
days while the whole suite stayed green. The catalog said 16, production served 13, and the
three newest cards rendered a flat category gradient because the bundled JSON (the offline
fallback) deliberately carries no imageUrl. Nothing anywhere compared what was authored to
what was produced, so there was no signal at all.

These checks are LOCAL and offline by design (`.claude/rules/testing.md`: no Supabase, no
network in the suite). They cannot see the database — that is what
`scripts/check_money_moves_published.py` is for, and it is the probe to run after seeding.
What they CAN catch is the upstream half of the same failure: an article authored without
being taken through the `generate → align → seed` playbook in
`.claude/skills/add-learn-content/SKILL.md`.
"""

import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND_JSON = _REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
_BACKEND_JSON = _REPO / "backend/data/money_moves.json"
_ART = _REPO / "backend/data/money_moves_art"
_AUDIO = _REPO / "backend/data/money_moves_audio"


def _articles(path: Path) -> list[dict]:
    if not path.exists():
        pytest.fail(f"catalog is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))["articles"]


def test_the_two_catalog_copies_do_not_drift():
    """`seed_money_moves.py` prefers the FRONTEND copy (`JSON_PATH`), while Railway vendors the
    backend one. If they diverge, what gets published is not what the running server ships as
    its fallback — and the difference is invisible until a reader sees two different libraries.
    """
    front = {a["slug"] for a in _articles(_FRONTEND_JSON)}
    back = {a["slug"] for a in _articles(_BACKEND_JSON)}
    assert front == back, (
        "money_moves.json has drifted between the iOS bundle and backend/data. "
        f"frontend-only={sorted(front - back)} backend-only={sorted(back - front)}. "
        "seed_money_moves.py publishes the FRONTEND copy; keep them identical."
    )


def test_every_authored_article_has_generated_artwork():
    """Art is unconditional for a Money Moves article: the catalog tile, the article header and
    the See-All featured card all render it, and a missing `imageUrl` silently degrades to the
    authored gradient with no error anywhere. Migration 137 documents both sizes.
    """
    missing = []
    for a in _articles(_FRONTEND_JSON):
        slug = a["slug"]
        for suffix in ("hero.jpg", "card.jpg", "manifest.json"):
            if not (_ART / f"{slug}.{suffix}").exists():
                missing.append(f"{slug}.{suffix}")
    assert not missing, (
        "authored article(s) have no generated artwork: " + ", ".join(missing) +
        " — run scripts/generate_money_moves_art.py, then scripts/seed_money_moves.py."
    )


def test_generated_artwork_records_an_upload():
    """A manifest without an `uploaded` record means the plate exists only on this machine.

    Anti-vacuity note: this is NOT proof the object is in the bucket today — it is proof the
    publish step ran for this slug at least once. The bucket itself is checked by
    scripts/check_money_moves_published.py, which does hit the network.
    """
    unpublished = []
    for a in _articles(_FRONTEND_JSON):
        slug = a["slug"]
        manifest = json.loads((_ART / f"{slug}.manifest.json").read_text(encoding="utf-8"))
        uploaded = manifest.get("uploaded") or {}
        if not (uploaded.get("hero") and uploaded.get("card")):
            unpublished.append(slug)
    assert not unpublished, (
        "artwork never uploaded for: " + ", ".join(unpublished) +
        " — scripts/seed_money_moves.py uploads and records the sha256 per size."
    )


def test_articles_claiming_narration_have_a_local_clip():
    """`seed_money_moves.py` sets `audio_url` from a LOCAL .m4a (or an object already in the
    bucket). An article that declares `hasAudioVersion` with neither leaves the Listen control
    on screen and nothing behind it.
    """
    missing = [
        a["slug"] for a in _articles(_FRONTEND_JSON)
        if a.get("hasAudioVersion") and not (_AUDIO / f"{a['slug']}.m4a").exists()
    ]
    assert not missing, (
        "hasAudioVersion is set but no local clip exists for: " + ", ".join(missing) +
        " — run scripts/generate_money_moves_audio.py then align_money_moves_audio.py."
    )
