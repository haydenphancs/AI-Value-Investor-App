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
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND_JSON = _REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
_BACKEND_JSON = _REPO / "backend/data/money_moves.json"
_ART = _REPO / "backend/data/money_moves_art"
_AUDIO = _REPO / "backend/data/money_moves_audio"
_SCRIPTS = _REPO / "backend/scripts"

# Articles deliberately shipped silent. MUST normally be empty: an entry here is a promise to
# come back, not a way to quiet the guard below.
_NARRATION_PENDING: set[str] = set()

# The catalog's voice is the Chatterbox clone (`caydex_voice_achird_v2`) — the SAME reference and
# settings the Investor Journey uses, because `REF`/`EXAG`/`CFG`/`TARGET_WPM` are module-level
# constants in clone_learn_audio.py shared by both of its modes. Both fingerprints below are
# derived from the file size and the authored duration ONLY, so the suite stays hermetic: no
# ffmpeg, no numpy, no network. Measured 2026-08-22 across the whole catalog:
#   clone  -> 12073-12245 B/s, 150.4-159.2 WPM   (13 clips)
#   Gemini ->  9635-9705  B/s, 169.9-170.4 WPM   (3 clips, the drift this catches)
_CLONE_BYTES_PER_SEC = (11_500, 12_800)   # -b:a 96k -> ~12.1k B/s; the Gemini default is ~9.7k
_CLONE_WPM = (144.0, 165.0)               # TARGET_WPM 165 lands ~158 after block pauses;
                                          # the-future-of-digital-finance was re-speeded to 150


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
        " — run clone_learn_audio.py (see .claude/skills/add-learn-content/SKILL.md), then align."
    )


def test_narration_pending_lists_only_real_slugs():
    """A typo in `_NARRATION_PENDING` silences nothing, but it also records a promise against an
    article that does not exist — and it hides that the real slug is still unnarrated."""
    slugs = {a["slug"] for a in _articles(_FRONTEND_JSON)}
    unknown = sorted(_NARRATION_PENDING - slugs)
    assert not unknown, f"_NARRATION_PENDING names slug(s) not in the catalog: {unknown}"


def test_every_authored_article_has_narration():
    """The inverse of the check above — and the half that was missing.

    Seven articles (Boeing vs. Airbus among them) were authored, seeded and served with no clip
    at all. `hasAudioVersion` was honestly `false`, so the check above stayed green while the
    Listen control silently vanished for nearly a third of the catalog. A TestFlight tester
    found it, not the suite. Narration is not optional for a Money Moves article: it is half of
    a read-and-listen feature, and the article renders with no error to say it is missing.
    """
    silent = sorted(
        a["slug"] for a in _articles(_FRONTEND_JSON)
        if a["slug"] not in _NARRATION_PENDING and not (_AUDIO / f"{a['slug']}.m4a").exists()
    )
    assert not silent, (
        "authored article(s) have no narration clip: " + ", ".join(silent) +
        " — CLONE_MODE=block ./venv_clone/bin/python scripts/clone_learn_audio.py moneymoves"
        " <slug>, move it into backend/data/money_moves_audio/, then align + seed."
    )


def _narration_blocks():
    """The REAL `narration_blocks` from the generator, loaded by source rather than mirrored.

    A hand-copied word-counter here would be a fourth copy of the spoken-block rules (after the
    generator, the cloner and the aligner) and would drift — which is the very failure
    test_money_moves_alignment_parity.py exists to catch. The module cannot simply be imported:
    it asserts a GEMINI_API_KEY at import time. `narration_blocks` needs only `re`.
    """
    src = (_SCRIPTS / "generate_money_moves_audio.py").read_text(encoding="utf-8")
    ns: dict = {"re": re}
    for name in ("strip_markup", "narration_blocks"):
        m = re.search(rf"\ndef {name}\b", src)
        assert m, f"{name}() not found in generate_money_moves_audio.py — did it move/rename?"
        nxt = re.search(r"\ndef \w+", src[m.start() + 1:])
        body = src[m.start(): m.start() + 1 + nxt.start()] if nxt else src[m.start():]
        exec(compile(body, "<narration_blocks>", "exec"), ns)
    return ns["narration_blocks"]


def test_narration_matches_the_investor_journey_voice():
    """Every clip must carry the SAME voice and pace as the Investor Journey.

    Three clips (nvidias-ai-dominance, the-fall-of-sears, amd-vs-intel-the-cpu-wars) were made
    with Gemini `Achird` instead of the clone because SKILL.md Step 2 named the wrong script.
    Measured, they sit ~8% lower in pitch, flatter, 7% faster and at ~77 kbps — audibly a
    different narrator mid-catalog, and nothing caught it for a week.

    Pitch needs a decoder, so this pins the two proxies that do NOT: the encode (bytes/sec, set
    by the clone's explicit `-b:a 96k` versus ffmpeg's ~77 kbps default) and the pace (spoken
    words over the authored duration, `TARGET_WPM`). Both separated the two engines with a wide
    margin on real data — see the constants above.
    """
    blocks = _narration_blocks()
    bad: list[str] = []
    for a in _articles(_FRONTEND_JSON):
        slug = a["slug"]
        clip = _AUDIO / f"{slug}.m4a"
        if not clip.exists():
            continue                      # test_every_authored_article_has_narration owns this
        dur = a.get("audioDurationSeconds")
        if not dur:
            bad.append(f"{slug}: has a clip but no audioDurationSeconds (never aligned)")
            continue
        bps = clip.stat().st_size / dur
        wpm = sum(len(b.split()) for b in blocks(a)) / dur * 60
        if not _CLONE_BYTES_PER_SEC[0] <= bps <= _CLONE_BYTES_PER_SEC[1]:
            bad.append(f"{slug}: {bps:.0f} B/s outside clone range {_CLONE_BYTES_PER_SEC}")
        if not _CLONE_WPM[0] <= wpm <= _CLONE_WPM[1]:
            bad.append(f"{slug}: {wpm:.1f} WPM outside clone range {_CLONE_WPM}")
    assert not bad, (
        "narration does not match the Investor Journey voice:\n  " + "\n  ".join(bad) +
        "\nRegenerate with CLONE_MODE=block scripts/clone_learn_audio.py (ref"
        " caydex_voice_achird_v2.wav) — NOT generate_money_moves_audio.py."
    )


def test_narration_flags_are_coherent():
    """A clip, its duration and the flag that reveals the player must agree.

    These are set by three different steps — the cloner writes the .m4a, the aligner writes
    `audioDurationSeconds`, and `seed_money_moves.py` derives `hasAudioVersion` from the UPLOAD
    (it writes the DB row, never back to this JSON). So a half-finished run leaves a coherent-
    looking catalog that is actually inconsistent, and the inconsistency is invisible: production
    reads the `audio_url` COLUMN, so the JSON can disagree with reality indefinitely and only the
    offline fallback and the next reader of this file are misled.
    """
    incoherent = []
    for a in _articles(_FRONTEND_JSON):
        slug = a["slug"]
        has_clip = (_AUDIO / f"{slug}.m4a").exists()
        flag = bool(a.get("hasAudioVersion"))
        dur = a.get("audioDurationSeconds")
        if has_clip and not flag:
            incoherent.append(f"{slug}: clip on disk but hasAudioVersion is false")
        if has_clip and not dur:
            incoherent.append(f"{slug}: clip on disk but no audioDurationSeconds (align never ran)")
        if dur and not has_clip:
            incoherent.append(f"{slug}: audioDurationSeconds={dur} but no clip — stale timing")
    assert not incoherent, "narration metadata disagrees with the clips:\n  " + "\n  ".join(incoherent)
