"""Weekly AI-written Learn pieces stay OUT of the bundle and out of the public marketing pool.

Plan "Wiser weekly content engine" §1.10. A weekly piece (the Friday Feature Deep Dive, later
the category slot) is written by a model from an SEC fact sheet, approved by the owner in
Telegram, and lives ONLY in `money_move_articles` (`source = 'weekly'`). Two things must never
happen to it:

1. **It lands in a bundled `money_moves.json`.** The bundle is the iOS offline fallback AND the
   source the marketing content pool reads (`marketing/content_pool.py`). A weekly piece copied
   into it would ship inside an app binary (no unpublish possible) and become class-A public
   marketing material that the marketing validators, judge and disclosures never covered.
2. **It enters the marketing pool by any other path.** `content_pool` refuses a Money Moves item
   whose slug has the weekly shape (`<kebab-title>-<yyyy-mm-dd>`) or whose row says
   `source: "weekly"` — the second lock, in case (1) is ever broken.

Also pinned here: `money_moves_content_service.NEVER_FEATURE` covers every Money Moves item the
marketing pool hand-excludes, so the featured rule's fall-back (an unpublish re-features the
previous flag) can never put one of them in the hero slot — and the serving path does not import
the marketing package to get that list.

Hermetic: reads the two bundled JSON files and synthetic temp files only.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.services import money_moves_content_service as mm
from app.services.marketing import content_pool as cp

_REPO = Path(__file__).resolve().parents[2]
_BUNDLES = (
    _REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json",
    _REPO / "backend/data/money_moves.json",
)
_SERVICE = _REPO / "backend/app/services/money_moves_content_service.py"

# The weekly slug shape, written independently of `content_pool._WEEKLY_SLUG_RE` so a loosened
# production regex cannot make this check agree with it by construction.
_WEEKLY_SLUG = re.compile(r"-(19|20)\d\d-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def _articles(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        pytest.fail(f"bundled catalog is missing: {path}")
    articles = json.loads(path.read_text(encoding="utf-8"))["articles"]
    assert len(articles) >= 20, f"{path.name}: implausibly small catalog — the guard went vacuous"
    return articles


# ── 1. the bundles carry no weekly piece ─────────────────────────────────────────────────────


@pytest.mark.parametrize("path", _BUNDLES, ids=lambda p: "frontend" if "frontend" in p.parts else "backend")
def test_no_bundled_article_has_the_weekly_slug_shape(path: Path):
    offenders = [a.get("slug") for a in _articles(path)
                 if _WEEKLY_SLUG.search(str(a.get("slug") or "").strip())]
    assert not offenders, (
        f"{path.name} carries weekly-shaped slug(s) {offenders}: weekly AI pieces live ONLY in "
        f"money_move_articles — never copy one into the bundle"
    )


@pytest.mark.parametrize("path", _BUNDLES, ids=lambda p: "frontend" if "frontend" in p.parts else "backend")
def test_no_bundled_article_says_source_weekly(path: Path):
    offenders = [a.get("slug") for a in _articles(path)
                 if str(a.get("source") or "").strip().lower() == "weekly"]
    assert not offenders, f"{path.name} carries source=weekly article(s): {offenders}"
    # The raw text too: a `source` nested where the scan above does not look is still a leak.
    assert not re.search(r'"source"\s*:\s*"weekly"', path.read_text(encoding="utf-8"), re.I)


@pytest.mark.parametrize("path", _BUNDLES, ids=lambda p: "frontend" if "frontend" in p.parts else "backend")
def test_the_bundle_flags_exactly_one_hero_and_it_is_featurable(path: Path):
    """The featured rule WARNs on more than one flagged bundle row and never features a
    NEVER_FEATURE slug; the bundle itself must satisfy both (plan Phase 0 moved the flag off
    `the-future-of-digital-finance`)."""
    flagged = [a["slug"] for a in _articles(path) if a.get("isFeatured") is True]
    assert len(flagged) == 1, f"{path.name}: {len(flagged)} articles flagged isFeatured: {flagged}"
    assert flagged[0] not in mm.NEVER_FEATURE, (
        f"{path.name}: the bundled hero {flagged[0]!r} is on NEVER_FEATURE — the server would "
        f"serve no hero at all"
    )


# ── 2. the marketing pool refuses weekly pieces ──────────────────────────────────────────────


@pytest.mark.parametrize("article, refused", [
    ({"slug": "power-for-ai-2026-10-23"}, True),
    ({"slug": "power-for-ai-2026-10-23  "}, True),                  # stray whitespace
    ({"slug": "how-amazon-built-its-moat", "source": "weekly"}, True),
    ({"slug": "how-amazon-built-its-moat", "source": " Weekly "}, True),
    ({"slug": "how-amazon-built-its-moat", "source": "bundle"}, False),
    ({"slug": "how-amazon-built-its-moat"}, False),
    ({"slug": "the-2008-crisis"}, False),                           # a year is not a date
    ({"slug": "q3-2026-10"}, False),                                # no day
    ({"slug": "2026-10-23-recap"}, False),                          # the date must END the slug
    ({"slug": None, "source": None}, False),
    ({"slug": 20261023, "source": 1}, False),                       # wrong types: no crash
    ({}, False),
])
def test_weekly_piece_reason(article, refused):
    assert (cp.weekly_piece_reason(article) is not None) is refused


def _synthetic_article(slug: str, **extra: Any) -> Dict[str, Any]:
    body = ("A warehouse club charges a yearly membership fee and sells goods close to cost. "
            "The fee carries most of the profit while low prices keep members renewing. ") * 12
    article = {
        "slug": slug, "title": f"Synthetic {slug}", "subtitle": "A synthetic case study.",
        "category": "blueprints",
        "sections": [{"title": "How it works", "content": [{"type": "paragraph", "text": body}]}],
    }
    article.update(extra)
    return article


@pytest.fixture
def swap_money_moves(monkeypatch, tmp_path):
    """Point the pool's Money Moves file at a synthetic one; the corpus cache is cleared on the
    way in and out so no other test sees the synthetic pool."""
    cp.load_corpus.cache_clear()

    def _swap(articles: List[Dict[str, Any]]) -> None:
        path = tmp_path / "money_moves.json"
        path.write_text(json.dumps({"articles": articles}), encoding="utf-8")
        monkeypatch.setattr(cp, "MONEY_MOVES_PATH", path)
        cp.load_corpus.cache_clear()

    try:
        yield _swap
    finally:
        cp.load_corpus.cache_clear()


def test_the_pool_refuses_weekly_pieces_and_names_them(swap_money_moves, caplog):
    swap_money_moves([
        _synthetic_article("warehouse-club-basics"),
        _synthetic_article("power-for-ai-2026-10-23"),
        _synthetic_article("ai-chips-explained", source="weekly"),
    ])
    with caplog.at_level(logging.WARNING, logger=cp.logger.name):
        keys = {key for key, *_rest in cp._raw_items() if key.startswith("money_moves:")}
        corpus = cp.load_corpus()
    assert keys == {"money_moves:warehouse-club-basics"}
    assert "money_moves:power-for-ai-2026-10-23" not in corpus
    assert "money_moves:ai-chips-explained" not in corpus
    assert "money_moves:warehouse-club-basics" in corpus, "the refusal must not over-block"
    warned = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert "power-for-ai-2026-10-23" in warned and "ai-chips-explained" in warned, \
        "every refused weekly piece must be named in the logs"


def test_the_real_pool_is_back_after_the_swap():
    """Runs after the swap above: a leaked synthetic corpus would break every marketing test."""
    assert cp.get_item("money_moves:how-amazon-built-its-moat") is not None
    assert not any(_WEEKLY_SLUG.search(k) for k in cp.load_corpus())


# ── 3. NEVER_FEATURE covers the marketing exclusions, without importing marketing ────────────


def test_never_feature_contains_every_excluded_money_moves_item():
    excluded = {key.split(":", 1)[1] for key in cp.EXCLUDED if key.startswith(f"{cp.MONEY_MOVES}:")}
    assert excluded, "no Money Moves exclusions found — the guard went vacuous"
    missing = sorted(excluded - mm.NEVER_FEATURE)
    assert not missing, (
        f"content_pool.EXCLUDED gained Money Moves item(s) {missing} that NEVER_FEATURE lacks: "
        f"an unpublish could re-feature them as the Learn hero. Add them to NEVER_FEATURE."
    )


def test_every_never_feature_slug_exists_in_the_bundle():
    """A typo in NEVER_FEATURE protects nothing."""
    slugs = {a["slug"] for a in _articles(_BUNDLES[1])}
    stale = sorted(mm.NEVER_FEATURE - slugs)
    assert not stale, f"NEVER_FEATURE names slug(s) not in the catalog: {stale}"


def _imported_modules(source: str) -> List[str]:
    """Every module the source imports — top level AND inside functions (a lazy import is
    still a runtime import), from the AST, so comments and strings never count."""
    out: List[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            out.append(base)
            out.extend(f"{base}.{alias.name}" for alias in node.names)
    return out


def _marketing_imports(source: str) -> List[str]:
    return [m for m in _imported_modules(source)
            if m.startswith("app.services.marketing") or m.endswith("content_pool")]


def test_the_serving_path_does_not_import_the_marketing_package():
    """NEVER_FEATURE is COPIED, not imported: `content_pool` builds the marketing compliance and
    grounding tables at import, which the Learn content route must not pay for (or fail on)."""
    offenders = _marketing_imports(_SERVICE.read_text(encoding="utf-8"))
    assert not offenders, f"money_moves_content_service imports {offenders}"


def test_the_bundle_slug_detector_is_not_vacuous():
    assert _WEEKLY_SLUG.search("power-for-ai-2026-10-23")
    assert _WEEKLY_SLUG.search("year-end-2026-12-31")
    assert not _WEEKLY_SLUG.search("how-amazon-built-its-moat")
    assert not _WEEKLY_SLUG.search("the-2008-crisis")
    # Every weekly slug this test's regex flags, production's refusal flags too.
    for slug in ("a-2026-10-23", "b-1999-01-01", "c-2027-01-08"):
        assert cp.weekly_piece_reason({"slug": slug}) is not None


def test_the_import_detector_is_not_vacuous():
    assert _marketing_imports("from app.services.marketing.content_pool import EXCLUDED\n")
    assert _marketing_imports("from app.services.marketing import content_pool\n")
    assert _marketing_imports("def f():\n    import app.services.marketing.content_pool as cp\n")
    assert not _marketing_imports("# from app.services.marketing import content_pool\n")
    assert not _marketing_imports("X = 'app.services.marketing.content_pool'\n")
