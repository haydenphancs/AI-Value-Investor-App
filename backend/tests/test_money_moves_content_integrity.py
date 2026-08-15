"""Money Moves content must not claim things that aren't true.

Two defects this pins, both found 2026-08-14 and both App Store Guideline 2.3.1 / 1.1.6
("accurate metadata", no false information) as much as they are product bugs:

1. **36 FABRICATED `viewCount` values** ("4.2M", "3.1M", "2.8M"…) on `relatedArticles`, while
   all 13 real articles carried `""`. Nothing counts views — the numbers were invented to make
   the carousel look busy. The card already hides an empty count, so blanking them is the whole
   fix; there is no view-count feature to wire up.

2. **A dangling entry.** "How AI Is Revolutionizing Stock Market Analysis" appeared in two
   articles' `relatedArticles` and does not exist. Now that tapping a card actually navigates
   (`MoneyMoveArticleDetailView.openRelated`, resolved BY TITLE because these entries carry no
   slug), a dangling title is a tap that silently does nothing.

This file guards the bundled JSON, which is the OFFLINE FALLBACK. The live content is the
Supabase row — so fixing the JSON is necessary but NOT sufficient: re-run
`backend/scripts/seed_money_moves.py` to publish, per `.claude/rules/learn-content.md`.

3. **THE VENDORED COPY DRIFTED.** Every check here originally scanned only the frontend file,
   so `backend/data/money_moves.json` kept all 36 fabricated view counts for months. That copy
   is not dead weight: `seed_money_moves.py`, `generate_money_moves_audio.py` and
   `align_money_moves_audio.py` all resolve
   `JSON_PATH = frontend_json if frontend_json.exists() else backend/data/money_moves.json`,
   so a backend-only checkout — Railway, a slim CI image — seeds the vendored one straight
   into production. `test_vendored_backend_copy_matches_the_frontend_bundle` below closes it.
"""

import json
from pathlib import Path

import pytest

_JSON = (
    Path(__file__).resolve().parents[2]
    / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
)
_VENDORED = Path(__file__).resolve().parents[1] / "data/money_moves.json"


@pytest.fixture(scope="module")
def content() -> dict:
    if not _JSON.exists():
        pytest.fail(f"bundled Money Moves content is missing: {_JSON}")
    return json.loads(_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def articles(content) -> list[dict]:
    arts = content.get("articles") or []
    assert len(arts) >= 10, f"only {len(arts)} articles — scan drifted or content was truncated"
    return arts


def _related(articles):
    for art in articles:
        for rel in art.get("relatedArticles") or []:
            yield art, rel


def test_no_fabricated_view_counts(articles):
    """Nothing in the app counts views, so any non-empty value here is invented."""
    bogus = sorted(
        {
            f"{rel.get('viewCount')!r} on {rel.get('title')!r} (in {art.get('title')!r})"
            for art, rel in _related(articles)
            if (rel.get("viewCount") or "").strip()
        }
    )
    assert not bogus, (
        "relatedArticles carry non-empty viewCount values, but no view counting exists — "
        "these are fabricated engagement numbers shown to users:\n  " + "\n  ".join(bogus)
    )


def test_articles_themselves_declare_no_view_count(articles):
    bogus = sorted(a["title"] for a in articles if (a.get("viewCount") or "").strip())
    assert not bogus, f"articles with a fabricated viewCount: {bogus}"


def test_every_related_article_resolves_to_a_real_article(articles):
    """`openRelated` resolves BY TITLE (these entries have no slug). A title with no article
    is a card that looks tappable and does nothing."""
    real = {a["title"] for a in articles}
    dangling = sorted(
        {
            f"{rel.get('title')!r} (linked from {art.get('title')!r})"
            for art, rel in _related(articles)
            if rel.get("title") not in real
        }
    )
    assert not dangling, (
        "relatedArticles reference titles that do not exist:\n  " + "\n  ".join(dangling)
        + "\nTapping these navigates nowhere. Either add the article or point the card at a "
        "real one."
    )


def test_no_article_lists_itself_as_related(articles):
    selfrefs = [
        a["title"]
        for a in articles
        if a["title"] in {r.get("title") for r in (a.get("relatedArticles") or [])}
    ]
    assert not selfrefs, f"articles listing themselves under Related Articles: {selfrefs}"


def test_related_entries_carry_the_fields_the_card_renders(articles):
    """`RelatedMoneyMoveCard` reads title/subtitle/category/readTimeMinutes. A missing one
    renders as a blank or a zero, not as an error."""
    broken = []
    for art, rel in _related(articles):
        for field in ("title", "subtitle", "category"):
            if not (rel.get(field) or "").strip():
                broken.append(f"{art['title']!r} -> related entry missing {field}")
        if not isinstance(rel.get("readTimeMinutes"), int) or rel["readTimeMinutes"] <= 0:
            broken.append(
                f"{art['title']!r} -> {rel.get('title')!r} has readTimeMinutes="
                f"{rel.get('readTimeMinutes')!r}"
            )
    assert not broken, "\n  " + "\n  ".join(sorted(set(broken)))


def test_related_read_time_matches_the_real_article(articles):
    """The card shows a read time. If it disagrees with the article it opens, the number the
    user chose on is wrong the moment they tap."""
    by_title = {a["title"]: a for a in articles}
    mismatches = []
    for art, rel in _related(articles):
        target = by_title.get(rel.get("title"))
        if target and target.get("readTimeMinutes") != rel.get("readTimeMinutes"):
            mismatches.append(
                f"{rel['title']!r}: card says {rel.get('readTimeMinutes')} min, "
                f"article says {target.get('readTimeMinutes')} min"
            )
    assert not mismatches, "\n  " + "\n  ".join(sorted(set(mismatches)))


def test_every_article_is_reachable_from_another_article(articles):
    """No islands. `relatedArticles` is the only in-article navigation there is, so an article
    nothing links to can be reached only by scrolling the catalog rows — which is exactly what
    happens to a newly authored one if the back-links are forgotten."""
    titles = {a["title"] for a in articles}
    linked = {rel.get("title") for _, rel in _related(articles)}
    orphans = sorted(titles - linked)
    assert not orphans, (
        "no other article links to these, so they are unreachable while reading:\n  "
        + "\n  ".join(orphans)
        + "\n  → add a relatedArticles entry pointing at each (append; don't replace a "
          "reviewed link), matching the target's real readTimeMinutes."
    )


def test_vendored_backend_copy_matches_the_frontend_bundle():
    """`backend/data/money_moves.json` must be byte-equivalent content to the frontend bundle.

    It is NOT a stale mirror that nobody reads: all three scripts fall back to it when
    `frontend/` is absent, which is the normal shape of a backend-only checkout. When these
    two drifted, the vendored copy still held the 36 fabricated view counts this file was
    written to eliminate — and a seed run from that checkout would have republished them.

    `align_money_moves_audio.py` rewrites BOTH files on every run, so the repair is a side
    effect of aligning; this test is what stops it drifting again in between.
    """
    if not _VENDORED.exists():
        pytest.skip("no vendored copy in this checkout")
    frontend = json.loads(_JSON.read_text(encoding="utf-8"))
    vendored = json.loads(_VENDORED.read_text(encoding="utf-8"))

    f_slugs = [a["slug"] for a in frontend["articles"]]
    v_slugs = [a["slug"] for a in vendored["articles"]]
    assert v_slugs == f_slugs, (
        f"vendored copy has different articles — only in frontend: "
        f"{sorted(set(f_slugs) - set(v_slugs))}, only in vendored: "
        f"{sorted(set(v_slugs) - set(f_slugs))}"
    )
    if frontend == vendored:
        return
    # Same articles, different content — report the first differing field per slug rather
    # than dumping two 250 KB blobs.
    diffs = []
    for fa, va in zip(frontend["articles"], vendored["articles"]):
        for key in sorted(set(fa) | set(va)):
            if fa.get(key) != va.get(key):
                diffs.append(f"{fa['slug']}.{key}")
    assert not diffs, (
        "vendored backend copy has drifted from the frontend bundle at:\n  "
        + "\n  ".join(diffs[:40])
        + f"\n  ({len(diffs)} field(s) total)\n"
        "  → re-run scripts/align_money_moves_audio.py, which rewrites both, and commit both."
    )


# ── Anti-vacuity ───────────────────────────────────────────────────────────────


def test_the_scan_actually_sees_related_entries(articles):
    pairs = list(_related(articles))
    assert len(pairs) >= 30, f"only {len(pairs)} related entries found — scan drifted"
    assert len({r.get("title") for _, r in pairs}) >= 10
