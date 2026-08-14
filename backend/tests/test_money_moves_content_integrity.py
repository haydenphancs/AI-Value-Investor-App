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
"""

import json
from pathlib import Path

import pytest

_JSON = (
    Path(__file__).resolve().parents[2]
    / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
)


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


# ── Anti-vacuity ───────────────────────────────────────────────────────────────


def test_the_scan_actually_sees_related_entries(articles):
    pairs = list(_related(articles))
    assert len(pairs) >= 30, f"only {len(pairs)} related entries found — scan drifted"
    assert len({r.get("title") for _, r in pairs}) >= 10
