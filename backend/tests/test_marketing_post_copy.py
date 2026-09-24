"""
Adversarial tests for the code-owned half of every public post
(`app/services/marketing/post_copy.py`): disclaimer, call to action, hashtags, length budgets.

Pinned promises (module docstring, rules/marketing.md §1, Phase 2 plan §6):
* every composed caption ENDS with its disclaimer, exactly once; long variant where there is room,
  short on X/Threads/Bluesky; publisher is "Caydex" (the developer is an Apple Individual
  account and terms.html says "operated by Caydex") — never "Caydex Inc.", never "financial
  advisor";
* CTA per platform: TikTok/Instagram "Link in bio."; X link-free unless allowed; everything
  else its own https://caydexinvest.com/go/<platform> smart link;
* hashtags only from the curated constants (a model-written tag is how names and tickers get in);
* the X/Threads/Bluesky body budget is the platform limit minus the code-owned suffix, measured
  the way the platform counts (X: URL = 23, CJK/emoji = 2), and nothing is ever sliced.

A test marked `# BUG:` asserts the promised behaviour and fails today.

Category 1 (pure): no network, no Supabase.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.schemas.marketing import POST_PLATFORMS
from app.services.marketing import post_copy as pc
from app.services.marketing.compliance import clean, scan_text

BACKEND = Path(__file__).resolve().parents[1]
TERMS_HTML = BACKEND / "app" / "templates" / "legal" / "terms.html"

DATE = date(2026, 9, 23)
COMPUTED = ("x", "threads", "bluesky")
VIDEO_FIELDS = ("tiktok", "youtube_description", "instagram")
CATEGORIES = ("blueprints", "battles", "valueTraps", "foundation", "analysis", "strategies",
              "mastery", "unknown-category", "")
HOSTILE_CATEGORIES = ("#warrenbuffett", "buffett", "$AAPL", "valueTraps ", "BLUEPRINTS",
                      "link in bio", "\u0000")

BODIES = {
    "tiktok": "Three habits that quietly compound over a decade.",
    "youtube_title": "Three habits that compound",
    "youtube_description": "A short lesson on habits that compound over time.",
    "instagram": "Three habits that quietly compound.",
    "facebook": "Three habits that quietly compound over a decade.",
    "x": "Three habits that quietly compound.",
    "threads": "Three habits that quietly compound.",
    "bluesky": "Three habits that quietly compound.",
    "linkedin": "Three habits that quietly compound over a decade.",
}

CURATED_TAGS = frozenset(pc._CATEGORY_TAG.values()) | frozenset(pc._BASE_TAGS) | {"#investing"}


def _field(platform: str) -> str:
    return "youtube_description" if platform == "youtube" else platform


def _body_of_length(field: str, n: int, unit: str = "a") -> str:
    """A body whose platform-measured length is exactly `n` (unit weight must divide n)."""
    w = pc.measured_length(field, unit)
    body = unit * (n // w) + "a" * (n % w)
    assert pc.measured_length(field, body) == n
    return body


# ── the table itself ─────────────────────────────────────────────────────────


def test_every_caption_field_has_a_limit_and_every_platform_is_a_known_outlet():
    assert set(pc.LIMITS) == set(pc.CAPTION_FIELDS)
    assert set(pc.PLATFORMS) <= set(POST_PLATFORMS)
    assert pc.LIMITS["x"] == 280
    assert pc.LIMITS["bluesky"] == 300
    assert pc.LIMITS["threads"] == 500
    assert pc.LIMITS["youtube_title"] == 100


def test_every_body_cap_leaves_room_under_the_limit():
    for field in pc.CAPTION_FIELDS:
        assert 0 < pc.body_budget(field, "blueprints", DATE) <= pc.LIMITS[field], field


# ── disclaimer ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("platform", pc.PLATFORMS)
@pytest.mark.parametrize("category", ["blueprints", "foundation", "unknown-category"])
@pytest.mark.parametrize("allow_x_url", [False, True])
def test_disclaimer_is_present_exactly_once_and_last(platform, category, allow_x_url):
    post = pc.compose(platform, BODIES, category=category, run_date=DATE, allow_x_url=allow_x_url)
    disc = pc.disclaimer_for(_field(platform), DATE)
    assert disc
    assert post.caption.endswith(disc)
    assert post.caption.count(disc) == 1
    assert post.caption.startswith(BODIES[_field(platform)])   # never sliced
    assert pc.check_composed(post, DATE) == []


def test_youtube_title_carries_no_disclaimer_the_description_does():
    assert pc.disclaimer_for("youtube_title", DATE) is None
    post = pc.compose("youtube", BODIES, category="blueprints", run_date=DATE)
    assert post.title == BODIES["youtube_title"]
    assert post.caption.endswith(pc.disclaimer_for("youtube_description", DATE))


@pytest.mark.parametrize("field", pc.CAPTION_FIELDS)
def test_long_versus_short_variant_by_platform(field):
    got = pc.disclaimer_for(field, DATE)
    if field == "youtube_title":
        assert got is None
    elif field in COMPUTED:
        assert got == pc.disclaimer_short()
    else:
        assert got == pc.disclaimer_long(DATE, video=field in VIDEO_FIELDS)


def test_short_variant_is_shorter_than_long():
    assert len(pc.disclaimer_short()) < len(pc.disclaimer_long(DATE))


def _all_disclaimers(run_date: date):
    return {
        "long": pc.disclaimer_long(run_date),
        "long_video": pc.disclaimer_long(run_date, video=True),
        "short": pc.disclaimer_short(),
        "card": pc.disclaimer_card(run_date),
    }


@pytest.mark.parametrize("variant", ["long", "long_video", "short", "card"])
def test_disclaimer_content(variant):
    d = _all_disclaimers(DATE)[variant]
    low = d.lower()
    assert "not investment advice" in low
    assert re.search(r"\bAI\b", d), "AI disclosure missing"
    assert pc.PUBLISHER in d
    for banned in ("financial advisor", "financial adviser", "investment advisor",
                   "investment adviser"):
        assert banned not in low
    assert not re.search(r"caydex,?\s+inc", low)


def test_publisher_is_caydex():
    assert pc.PUBLISHER == "Caydex"


def test_video_and_text_disclose_ai_differently():
    assert "narration generated with AI" in pc.disclaimer_long(DATE, video=True)
    assert "Written with AI assistance" in pc.disclaimer_long(DATE, video=False)


@pytest.mark.parametrize("run_date, label", [
    (date(2026, 9, 23), "Sep 23, 2026"),
    (date(2027, 1, 5), "Jan 5, 2027"),
    (date(2028, 2, 29), "Feb 29, 2028"),
])
def test_dated_variants_carry_the_run_date(run_date, label):
    assert label in pc.disclaimer_long(run_date)
    assert label in pc.disclaimer_card(run_date)


def test_a_caption_checked_against_another_date_fails():
    post = pc.compose("tiktok", BODIES, category="blueprints", run_date=DATE)
    assert "disclaimer_missing" in [v.code for v in pc.check_composed(post, date(2026, 9, 24))]


# ── publisher ↔ terms.html ──────────────────────────────────────────────────


def _terms_text(path: Path = None) -> str:
    raw = (path or TERMS_HTML).read_text(encoding="utf-8")
    raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)          # comments say nothing binding
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text)


def test_terms_names_the_publisher_the_disclaimer_uses():
    text = _terms_text()
    assert pc.PUBLISHER in text
    assert f"operated by {pc.PUBLISHER}" in text


def test_no_caydex_inc_anywhere():
    pattern = re.compile(r"caydex,?\s+inc(?:\.|orporated|\b)", re.IGNORECASE)
    assert not pattern.search(_terms_text())
    texts = list(_all_disclaimers(DATE).values()) + [pc.PUBLISHER]
    texts += [pc.compose(p, BODIES, category="blueprints", run_date=DATE).caption
              for p in pc.PLATFORMS]
    for t in texts:
        assert not pattern.search(t), t


# ── call to action ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("field", ["tiktok", "instagram"])
def test_non_clickable_platforms_say_link_in_bio(field):
    assert pc.cta_for(field) == "Link in bio."
    assert pc.cta_for(field, allow_x_url=True) == "Link in bio."


def test_x_is_link_free_unless_allowed():
    assert pc.cta_for("x") is None
    caption = pc.compose("x", BODIES, category="blueprints", run_date=DATE).caption
    assert "http" not in caption and "caydexinvest" not in caption
    assert pc.cta_for("x", allow_x_url=True) == "https://caydexinvest.com/go/x"
    caption = pc.compose("x", BODIES, category="blueprints", run_date=DATE, allow_x_url=True).caption
    assert "https://caydexinvest.com/go/x" in caption


def test_youtube_title_has_no_cta_and_description_uses_the_youtube_campaign():
    assert pc.cta_for("youtube_title") is None
    assert pc.cta_for("youtube_description").endswith("https://caydexinvest.com/go/youtube")


@pytest.mark.parametrize("field, campaign", [
    ("youtube_description", "youtube"), ("facebook", "facebook"), ("threads", "threads"),
    ("bluesky", "bluesky"), ("linkedin", "linkedin"), ("x", "x"),
])
def test_smart_link_cta_shape(field, campaign):
    cta = pc.cta_for(field, allow_x_url=True)
    (url,) = re.findall(r"https?://\S+", cta)
    u = urlparse(url)
    assert u.scheme == "https"
    assert u.netloc == "caydexinvest.com"
    assert u.path == f"/go/{campaign}"
    assert not u.query and not u.fragment
    assert re.fullmatch(r"[a-z0-9_-]{1,40}", campaign)
    assert campaign in POST_PLATFORMS


@pytest.mark.parametrize("field", [f for f in pc.CAPTION_FIELDS if f != "x"])
def test_allow_x_url_changes_only_x(field):
    assert pc.cta_for(field) == pc.cta_for(field, allow_x_url=True)


@pytest.mark.parametrize("platform", pc.PLATFORMS)
def test_the_cta_is_in_the_composed_caption(platform):
    caption = pc.compose(platform, BODIES, category="blueprints", run_date=DATE).caption
    cta = pc.cta_for(_field(platform))
    if cta:
        assert cta in caption


# ── hashtags ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("field", pc.CAPTION_FIELDS)
@pytest.mark.parametrize("category", CATEGORIES + HOSTILE_CATEGORIES)
def test_hashtags_come_only_from_the_curated_constants(field, category):
    tags = pc.hashtags_for(field, category)
    assert set(tags) <= CURATED_TAGS
    assert len(tags) == len(set(tags))
    assert len(tags) <= 3
    if field == "x":
        assert len(tags) <= 1


def test_every_corpus_category_has_its_own_curated_tag():
    mm = json.loads((BACKEND / "data" / "money_moves.json").read_text(encoding="utf-8"))
    jl = json.loads((BACKEND / "data" / "journey_lessons.json").read_text(encoding="utf-8"))
    cats = {a.get("category") for a in mm["articles"]} | {l.get("level") for l in jl["lessons"]}
    cats.discard(None)
    assert cats
    assert cats <= set(pc._CATEGORY_TAG), cats - set(pc._CATEGORY_TAG)


@pytest.mark.parametrize("tag", sorted(CURATED_TAGS))
def test_every_curated_tag_passes_the_person_and_brand_scan(tag):
    got = {v.code for v in scan_text("f", clean(tag))}
    assert got <= {"hashtag"}, got
    assert re.fullmatch(r"#[a-z]{3,30}", tag)


@pytest.mark.parametrize("platform", pc.PLATFORMS)
@pytest.mark.parametrize("category", CATEGORIES + HOSTILE_CATEGORIES)
def test_composed_captions_carry_only_curated_tags(platform, category):
    caption = pc.compose(platform, BODIES, category=category, run_date=DATE).caption
    suffix = caption[len(BODIES[_field(platform)]):]
    assert set(re.findall(r"#\w+", suffix)) <= CURATED_TAGS


@pytest.mark.parametrize("platform", pc.PLATFORMS)
@pytest.mark.parametrize("category", [h for h in HOSTILE_CATEGORIES if h != "link in bio"])
def test_a_hostile_category_is_never_echoed(platform, category):
    caption = pc.compose(platform, BODIES, category=category, run_date=DATE).caption
    assert category not in caption


# ── length budgets ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("field", COMPUTED)
@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("allow_x_url", [False, True])
def test_a_body_of_exactly_the_budget_fits(field, category, allow_x_url):
    budget = pc.body_budget(field, category, DATE, allow_x_url=allow_x_url)
    assert budget >= pc._MIN_BODY
    bodies = dict(BODIES, **{field: _body_of_length(field, budget)})
    post = pc.compose(field, bodies, category=category, run_date=DATE, allow_x_url=allow_x_url)
    assert pc.measured_length(field, post.caption) <= pc.LIMITS[field]
    assert pc.check_composed(post, DATE) == []


@pytest.mark.parametrize("field", COMPUTED)
def test_budget_is_tight(field):
    budget = pc.body_budget(field, "blueprints", DATE)
    bodies = dict(BODIES, **{field: _body_of_length(field, budget + 1)})
    post = pc.compose(field, bodies, category="blueprints", run_date=DATE)
    assert pc.measured_length(field, post.caption) > pc.LIMITS[field]


@pytest.mark.parametrize("unit", ["\u65e5", "\U0001F680", "\u00e9"])
@pytest.mark.parametrize("allow_x_url", [False, True])
def test_x_budget_holds_with_weighted_characters(unit, allow_x_url):
    budget = pc.body_budget("x", "blueprints", DATE, allow_x_url=allow_x_url)
    body = _body_of_length("x", budget, unit)
    post = pc.compose("x", dict(BODIES, x=body), category="blueprints", run_date=DATE,
                      allow_x_url=allow_x_url)
    assert pc.x_weighted_length(post.caption) <= 280
    assert pc.check_composed(post, DATE) == []
    over = pc.compose("x", dict(BODIES, x=body + "\u65e5" * 10), category="blueprints",
                      run_date=DATE, allow_x_url=allow_x_url)
    assert "over_platform_limit" in [v.code for v in pc.check_composed(over, DATE)]


@pytest.mark.parametrize("field", [f for f in pc.CAPTION_FIELDS if f not in COMPUTED])
@pytest.mark.parametrize("category", CATEGORIES)
def test_a_body_at_its_editorial_cap_always_composes_within_the_limit(field, category):
    budget = pc.body_budget(field, category, DATE)
    bodies = dict(BODIES, **{field: "a" * budget})
    platform = "youtube" if field.startswith("youtube") else field
    post = pc.compose(platform, bodies, category=category, run_date=DATE)
    assert pc.check_composed(post, DATE) == []


# ── X weighting ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, n", [
    ("", 0),
    ("abc", 3),
    ("a b", 3),
    ("https://caydexinvest.com/go/x", 23),
    ("http://a.co", 23),
    ("see https://example.com/a-very-long-path-well-over-twenty-three-characters now", 31),
    ("\u65e5\u672c", 4),                      # CJK = 2
    ("\U0001F680", 2),                        # emoji = 2
    ("\u201cok\u201d", 4),                    # curly quotes are in the weight-1 ranges
    ("a\u2013b", 3),                          # en dash too
    ("caf\u00e9", 4),
])
def test_x_weighted_length(text, n):
    assert pc.x_weighted_length(text) == n


def test_x_weighted_length_tolerates_none():
    assert pc.x_weighted_length(None) == 0  # type: ignore[arg-type]


@pytest.mark.parametrize("text, n", [
    pytest.param("https://caydexinvest.com/go/x\nabc", 27, id="url_newline_text"),
    pytest.param("abc\nhttps://caydexinvest.com/go/x", 27, id="text_newline_url"),
    pytest.param("https://caydexinvest.com/go/x\n\n" + "a" * 300, 325, id="url_then_300_chars"),
])
def test_x_url_is_23_when_newline_separated(text, n):
    assert pc.x_weighted_length(text) == n


def test_bluesky_counts_code_points_which_is_at_least_graphemes():
    decomposed = "e\u0301"          # one grapheme, two code points
    assert pc.measured_length("bluesky", decomposed) == 2
    assert pc.measured_length("threads", decomposed) == 2


# ── check_composed ───────────────────────────────────────────────────────────


def test_missing_disclaimer_is_flagged():
    post = pc.ComposedPost("tiktok", None, "Just a body.")
    assert "disclaimer_missing" in [v.code for v in pc.check_composed(post, DATE)]


def test_duplicated_disclaimer_is_flagged():
    body = "Echoed: " + pc.disclaimer_short()
    post = pc.compose("x", dict(BODIES, x=body), category="blueprints", run_date=DATE)
    assert "disclaimer_missing" in [v.code for v in pc.check_composed(post, DATE)]


def test_text_after_the_disclaimer_is_flagged():
    post = pc.compose("linkedin", BODIES, category="blueprints", run_date=DATE)
    tampered = pc.ComposedPost(post.platform, post.title, post.caption + " Follow us.")
    assert "disclaimer_missing" in [v.code for v in pc.check_composed(tampered, DATE)]


@pytest.mark.parametrize("platform", pc.PLATFORMS)
def test_over_limit_caption_is_flagged_and_never_sliced(platform):
    field = _field(platform)
    big = "a" * (pc.LIMITS[field] + 1)
    post = pc.compose(platform, dict(BODIES, **{field: big}), category="blueprints", run_date=DATE)
    assert post.caption.startswith(big)
    assert "over_platform_limit" in [v.code for v in pc.check_composed(post, DATE)]


@pytest.mark.parametrize("title, ok", [("t" * 100, True), ("t" * 101, False), ("", False),
                                       (None, False), ("\u65e5" * 100, True)])
def test_youtube_title_limit(title, ok):
    desc = "Body." + "\n\n" + pc.disclaimer_for("youtube_description", DATE)
    post = pc.ComposedPost("youtube", title, desc)
    got = [v for v in pc.check_composed(post, DATE) if v.code == "over_platform_limit"]
    assert (got == []) is ok


def test_as_dict_shape():
    post = pc.compose("youtube", BODIES, category="blueprints", run_date=DATE)
    assert set(post.as_dict()) == {"platform", "title", "caption"}
    assert post.as_dict()["platform"] == "youtube"


# ── the legal text is pinned verbatim ────────────────────────────────────────

#: Golden values for DATE. Changing the legal text must be a deliberate edit HERE (and, for the
#: risk sentence, in the landing page's `_DISCLAIMER` in test_marketing_smart_link.py, so the
#: page and the posts stay consistent). Do NOT add the risk / "not a recommendation" clauses to
#: the short variant: X/Threads/Bluesky omit them on purpose, and every character there comes
#: out of the X body budget.
_GOLDEN = {
    "long": ("Caydex · Educational, impersonal information — not investment advice, not a "
             "recommendation, not an offer. Investing involves risk, including loss of principal. "
             "Written with AI assistance. Sep 23, 2026."),
    "long_video": ("Caydex · Educational, impersonal information — not investment advice, not a "
                   "recommendation, not an offer. Investing involves risk, including loss of "
                   "principal. Script and narration generated with AI. Sep 23, 2026."),
    "card": ("Educational, impersonal information — not investment advice. Investing involves "
             "risk. Script and narration generated with AI. Caydex · Sep 23, 2026"),
    "short": "Educational only, not investment advice. AI-assisted. Caydex",
}


@pytest.mark.parametrize("variant", sorted(_GOLDEN))
def test_disclaimer_wording_is_pinned_verbatim(variant):
    assert _all_disclaimers(DATE)[variant] == _GOLDEN[variant]


@pytest.mark.parametrize("variant", ["long", "long_video", "card"])
def test_the_risk_warning_is_in_every_long_disclaimer_and_the_video_card(variant):
    assert "Investing involves risk" in _all_disclaimers(DATE)[variant]


@pytest.mark.parametrize("variant", ["long", "long_video"])
def test_the_long_disclaimer_says_not_a_recommendation_not_an_offer(variant):
    assert "not a recommendation, not an offer" in _all_disclaimers(DATE)[variant]


# ── characters an outlet refuses (YouTube: '<', '>' anywhere; a title is one line) ──


def _yt(title: str = BODIES["youtube_title"], desc: str = BODIES["youtube_description"]) -> pc.ComposedPost:
    return pc.compose("youtube", dict(BODIES, youtube_title=title, youtube_description=desc),
                      category="blueprints", run_date=DATE)


def _forbidden(post: pc.ComposedPost):
    return [(v.field, v.code) for v in pc.check_composed(post, DATE) if v.code == "platform_forbidden_char"]


@pytest.mark.parametrize("title", [
    "Price > value?", "Myth -> fact", "< 5 minutes a day", "Mood swings <> business value",
    "Emotion < logic", "Two\nlines", "Two\r\nlines", "Two lines", "Two lines", "a\x85b",
])
def test_a_youtube_title_with_a_refused_character_is_flagged(title):
    assert _forbidden(_yt(title=title)) == [("youtube_title", "platform_forbidden_char")]


@pytest.mark.parametrize("desc", ["Patience > timing.", "Emotion < 5 minutes of thought.",
                                  "Fear -> greed -> regret."])
def test_a_youtube_description_with_a_bracket_is_flagged(desc):
    assert _forbidden(_yt(desc=desc)) == [("youtube_description", "platform_forbidden_char")]


@pytest.mark.parametrize("title, desc", [
    ("Fear → opportunity", "A lesson → in patience."),     # U+2192 is allowed
    ("‹angle› quotes", "Single ‹guillemets› are fine."),
    (BODIES["youtube_title"], "Paragraph one.\n\nParagraph two."),   # a description may break lines
])
def test_youtube_accepts_arrows_guillemets_and_description_paragraphs(title, desc):
    assert pc.check_composed(_yt(title=title, desc=desc), DATE) == []


def test_a_full_width_bracket_is_caught_once_cleaned_as_it_is_stored():
    title = clean("Price ＞ value")          # NFKC folds '＞' to '>' — that is what ships
    assert ">" in title
    assert _forbidden(_yt(title=title)) == [("youtube_title", "platform_forbidden_char")]


@pytest.mark.parametrize("platform", [p for p in pc.PLATFORMS if p != "youtube"])
def test_a_bracket_is_not_refused_on_other_outlets(platform):
    field = _field(platform)
    post = pc.compose(platform, dict(BODIES, **{field: "Patience > timing."}),
                      category="blueprints", run_date=DATE)
    assert pc.check_composed(post, DATE) == []


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("allow_x_url", [False, True])
def test_the_code_owned_suffix_never_contains_a_refused_character(category, allow_x_url):
    for field, bad in pc.FORBIDDEN_CHARS.items():
        suffix = pc._suffix(field, category, DATE, allow_x_url)
        assert not any(c in suffix for c in bad), (field, suffix)
    assert _forbidden(_yt()) == []


def test_the_refused_character_table_covers_every_line_separator_for_the_title():
    for sep in ("\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "):
        assert len(f"a{sep}b".splitlines()) == 2
        assert sep in pc.FORBIDDEN_CHARS["youtube_title"], repr(sep)


# ── why check_composed's LENGTH arm is a backstop today (and when it would not be) ──


def _eligible_categories():
    from app.services.marketing import content_pool

    cats = {content_pool.get_item(k).category for k in content_pool.eligible_keys()}
    assert cats
    return sorted(cats)


@pytest.mark.parametrize("allow_x_url", [False, True])
def test_every_budget_plus_its_suffix_fits_the_platform_and_never_hits_the_floor(allow_x_url):
    """A body at its budget always composes within the platform limit, and no computed budget is
    clamped up to _MIN_BODY (a clamp would make check_composed's length arm load-bearing — and
    silently drop that outlet every day). Fails the moment a suffix grows too long."""
    for category in _eligible_categories():
        for field in pc.CAPTION_FIELDS:
            suffix = pc._suffix(field, category, DATE, allow_x_url)
            budget = pc.body_budget(field, category, DATE, allow_x_url=allow_x_url)
            assert budget + pc.measured_length(field, suffix) <= pc.LIMITS[field], (field, category)
            if field in COMPUTED:
                assert pc.LIMITS[field] - pc.measured_length(field, suffix) > pc._MIN_BODY, (field, category)


# ── X counts a bare domain as a URL (defence in depth; content-A handoff) ─────


@pytest.mark.parametrize("text,expected", [
    ("investor.gov", 23),
    ("see investor.gov.", 4 + 23 + 1),                       # trailing period is not the domain
    ("a-very-very-long-subdomain.example-domain.com", 23),   # long → still 23
    ("Learn.Money", 23),                                     # cased gTLD, autolinked too
    ("U.S. e.g. i.e. 3.14 v2.0", len("U.S. e.g. i.e. 3.14 v2.0")),  # not domains
    ("plain words", 11),
    # Round 3 (W3VAC-10): a glued abbreviation the link validator allows and X cannot link is
    # text; one whose tail IS a gTLD X links keeps the URL weight (never under-count).
    ("U.S.dollar", 10), ("e.g.the", 7), ("vs.the", 6),
    ("U.S.markets", 23), ("e.g.bank", 23),
])
def test_x_weights_a_bare_domain_as_a_url(text, expected):
    assert pc.x_weighted_length(text) == expected
