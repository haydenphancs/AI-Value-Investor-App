"""
Adversarial tests for the code-owned half of every public post
(`app/services/marketing/post_copy.py`): disclaimer, call to action, hashtags, length budgets.

Pinned promises (module docstring, rules/marketing.md §1, Phase 2 plan §6):
* every composed caption ENDS with its disclaimer, exactly once; long variant where there is room,
  short on X/Threads/Bluesky; publisher is "Caydex" (the developer is an Apple Individual
  account and terms.html says "operated by Caydex") — never "Caydex Inc.", never "financial
  advisor";
* CTA per platform, opened by ONE code-owned VALUE LINE saying what Caydex is, worded by the
  store state threaded in (prelaunch the claim-free "Caydex: AI research on public companies.",
  preorder "… — pre-order on the App Store.", live "… — on the App Store."; anything unknown words
  as prelaunch, the line that claims least): TikTok/Instagram the line + "Link in bio."; X the line alone, link-free
  unless allowed; everything else the line + its own https://caydexinvest.com/go/<platform>
  smart link; the YouTube title carries none. Nothing scans the value line at runtime
  (compliance, grounding and the judge read only model text), so it is pinned verbatim here and
  run through the public-copy scan — those tests are its ONLY guard;
* `carries_go_link` is exactly the set of captions that carry their own /go link (the publisher
  opens a campaign's early /go window only for those);
* hashtags only from the curated constants (a model-written tag is how names and tickers get in);
* the X/Threads/Bluesky body budget is the platform limit minus the code-owned suffix (value line
  included, so it depends on the store state), measured the way the platform counts (X: URL = 23,
  CJK/emoji = 2), and nothing is ever sliced.

A test marked `# BUG:` asserts the promised behaviour and fails today.

Category 1 (pure): no network, no Supabase.
"""

from __future__ import annotations

import html
import json
import re
import string
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
STATES = pc.STORE_STATES
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
@pytest.mark.parametrize("state", STATES)
def test_disclaimer_is_present_exactly_once_and_last(platform, category, allow_x_url, state):
    post = pc.compose(platform, BODIES, category=category, run_date=DATE, allow_x_url=allow_x_url,
                      store_state=state)
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
    # The value line names the publisher too ("Caydex: AI research …"), in every store state.
    texts += [pc.value_line(s) for s in STATES]
    texts += [pc.compose(p, BODIES, category="blueprints", run_date=DATE, allow_x_url=a,
                         store_state=s).caption
              for p in pc.PLATFORMS for s in STATES for a in (False, True)]
    for t in texts:
        assert not pattern.search(t), t


# ── call to action ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("field", ["tiktok", "instagram"])
def test_non_clickable_platforms_say_link_in_bio(field, state):
    expected = f"{pc.value_line(state)} Link in bio."
    assert pc.cta_for(field, store_state=state) == expected
    assert pc.cta_for(field, allow_x_url=True, store_state=state) == expected
    assert "http" not in expected and "/go/" not in expected and pc.x_link_tokens(expected) == []


@pytest.mark.parametrize("field", ["tiktok", "instagram"])
def test_the_default_cta_is_the_prelaunch_line(field):
    """No state passed = prelaunch: the line that claims least (`cta_for`'s default)."""
    assert pc.cta_for(field) == "Caydex: AI research on public companies. Link in bio."
    assert pc.cta_for("x") == "Caydex: AI research on public companies."


@pytest.mark.parametrize("state", STATES)
def test_x_is_link_free_unless_allowed(state):
    assert pc.cta_for("x", store_state=state) == pc.value_line(state)
    caption = pc.compose("x", BODIES, category="blueprints", run_date=DATE, store_state=state).caption
    assert "http" not in caption and "caydexinvest" not in caption
    assert pc.x_link_tokens(caption) == []
    assert pc.cta_for("x", allow_x_url=True, store_state=state) == (
        f"{pc.value_line(state)} https://caydexinvest.com/go/x")
    caption = pc.compose("x", BODIES, category="blueprints", run_date=DATE, allow_x_url=True,
                         store_state=state).caption
    assert pc.x_link_tokens(caption) == ["https://caydexinvest.com/go/x"]


@pytest.mark.parametrize("state", STATES)
def test_youtube_title_has_no_cta_and_description_uses_the_youtube_campaign(state):
    assert pc.cta_for("youtube_title", store_state=state) is None
    assert pc.cta_for("youtube_title", allow_x_url=True, store_state=state) is None
    assert pc.cta_for("youtube_description", store_state=state) == (
        f"{pc.value_line(state)} Learn more: https://caydexinvest.com/go/youtube")


@pytest.mark.parametrize("field, campaign", [
    ("youtube_description", "youtube"), ("facebook", "facebook"), ("threads", "threads"),
    ("bluesky", "bluesky"), ("linkedin", "linkedin"), ("x", "x"),
])
@pytest.mark.parametrize("state", STATES)
def test_smart_link_cta_shape(field, campaign, state):
    cta = pc.cta_for(field, allow_x_url=True, store_state=state)
    (url,) = re.findall(r"https?://\S+", cta)
    u = urlparse(url)
    assert u.scheme == "https"
    assert u.netloc == "caydexinvest.com"
    assert u.path == f"/go/{campaign}"
    assert not u.query and not u.fragment
    assert re.fullmatch(r"[a-z0-9_-]{1,40}", campaign)
    assert campaign in POST_PLATFORMS
    # The link is the CTA's LAST token (nothing glued after it) and the value line opens it.
    assert cta.endswith(url) and cta.startswith(pc.value_line(state))
    assert url == pc.go_link(campaign)


@pytest.mark.parametrize("field", [f for f in pc.CAPTION_FIELDS if f != "x"])
@pytest.mark.parametrize("state", STATES)
def test_allow_x_url_changes_only_x(field, state):
    assert pc.cta_for(field, store_state=state) == pc.cta_for(field, allow_x_url=True, store_state=state)


@pytest.mark.parametrize("platform", pc.PLATFORMS)
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("allow_x_url", [False, True])
def test_the_cta_is_in_the_composed_caption(platform, state, allow_x_url):
    caption = pc.compose(platform, BODIES, category="blueprints", run_date=DATE,
                         allow_x_url=allow_x_url, store_state=state).caption
    cta = pc.cta_for(_field(platform), allow_x_url=allow_x_url, store_state=state)
    assert cta, platform   # every platform's caption field has a CTA (only the YouTube TITLE has none)
    assert caption.count(cta) == 1


# ── the value line: what Caydex is, worded by the store state ─────────────────
#
# Code-owned copy that NO runtime check scans: writer_service scans each model BODY before it is
# composed, check_composed checks only the disclaimer / length / refused characters, the judge reads
# only model text and create_posts copies the composed captions word for word. These tests are the
# line's only compliance guard — keep them strict (exact scan result, exact wording).

#: Owner wording (2026-10-05). Changing a line must be a deliberate edit HERE, and the new line must
#: pass test_every_value_line_passes_the_public_copy_scan unchanged. The prelaunch line lost its
#: "— coming soon to iPhone." on 2026-10-07 (review): the app is live, so prelaunch now means an unset
#: or mistyped store URL, and a caption must never call a released app "coming soon".
_VALUE_GOLDEN = {
    "prelaunch": "Caydex: AI research on public companies.",
    "preorder": "Caydex: AI research on public companies — pre-order on the App Store.",
    "live": "Caydex: AI research on public companies — on the App Store.",
}


def test_the_store_state_names_are_pinned():
    assert (pc.STORE_PRELAUNCH, pc.STORE_PREORDER, pc.STORE_LIVE) == ("prelaunch", "preorder", "live")
    assert pc.STORE_STATES == ("prelaunch", "preorder", "live")
    assert pc.VALUE_PRODUCT == "Caydex: AI research on public companies"


def test_smart_link_decides_with_the_same_three_names():
    """smart_link spells the names as its OWN literals (the request path never imports this module);
    a drift there (say "pre-order") would make every caption silently claim the prelaunch line."""
    from app.services.marketing import smart_link

    assert (smart_link.STORE_PRELAUNCH, smart_link.STORE_PREORDER, smart_link.STORE_LIVE) == (
        pc.STORE_PRELAUNCH, pc.STORE_PREORDER, pc.STORE_LIVE)
    assert {smart_link.STORE_PRELAUNCH, smart_link.STORE_PREORDER, smart_link.STORE_LIVE} == set(pc.STORE_STATES)


@pytest.mark.parametrize("state", sorted(_VALUE_GOLDEN))
def test_the_value_line_is_pinned_verbatim(state):
    assert set(_VALUE_GOLDEN) == set(pc.STORE_STATES)
    assert pc.value_line(state) == _VALUE_GOLDEN[state]
    if state == pc.STORE_PRELAUNCH:
        assert pc.value_line(state) == pc.VALUE_PRODUCT + "."
    else:
        assert pc.value_line(state).startswith(pc.VALUE_PRODUCT + " — ")


def test_the_prelaunch_line_makes_no_availability_claim():
    """Review 2026-10-07: prelaunch is reached after the release only through an unset or mistyped
    MARKETING_APP_STORE_URL, so its line may say what Caydex is and nothing about where or when."""
    low = pc.value_line(pc.STORE_PRELAUNCH).lower()
    for word in ("soon", "store", "iphone", "order", "launch", "coming", "app", "ios", "release"):
        assert not re.search(rf"(?<![a-z]){word}(?![a-z])", low), word


def test_the_default_value_line_is_prelaunch():
    assert pc.value_line() == _VALUE_GOLDEN["prelaunch"]
    assert pc.normalize_store_state(pc.STORE_PRELAUNCH) == "prelaunch"


#: What follows the value line in each caption field's CTA (design WORDING, 2026-10-05), as
#: (allow_x_url off, allow_x_url on): TikTok and Instagram " Link in bio."; X the line ALONE, or the
#: line + " " + its /go link only with allow_x_url; every other field " Learn more: " + its own /go
#: link (the YouTube description's campaign is "youtube"). The YouTube title has no CTA at all.
_CTA_TAILS = {
    "tiktok": (" Link in bio.", " Link in bio."),
    "instagram": (" Link in bio.", " Link in bio."),
    "x": ("", " https://caydexinvest.com/go/x"),
    "youtube_description": (" Learn more: https://caydexinvest.com/go/youtube",) * 2,
    "facebook": (" Learn more: https://caydexinvest.com/go/facebook",) * 2,
    "threads": (" Learn more: https://caydexinvest.com/go/threads",) * 2,
    "bluesky": (" Learn more: https://caydexinvest.com/go/bluesky",) * 2,
    "linkedin": (" Learn more: https://caydexinvest.com/go/linkedin",) * 2,
}


@pytest.mark.parametrize("allow_x_url", [False, True])
@pytest.mark.parametrize("state", STATES)
def test_every_cta_is_pinned_verbatim(state, allow_x_url):
    """Every field's whole CTA, character for character, in every state. The computed budgets pin
    only the X / Threads / Bluesky CTA LENGTHS; this pins the words of the Facebook, LinkedIn and
    YouTube-description CTAs too — code-owned copy nothing scans at runtime."""
    assert set(_CTA_TAILS) | {"youtube_title"} == set(pc.CAPTION_FIELDS)
    for field, tails in _CTA_TAILS.items():
        assert pc.cta_for(field, allow_x_url=allow_x_url, store_state=state) == (
            _VALUE_GOLDEN[state] + tails[allow_x_url]), field
    assert pc.cta_for("youtube_title", allow_x_url=allow_x_url, store_state=state) is None


#: Words a line about the app must never carry: a recommendation / performance / data-vendor word,
#: an availability claim the store page may not back ("now", "available", "download", "get it",
#: "free" — the live line must stay true on a pre-order page), or AI hype ("AI-powered picks").
_VALUE_DENYLIST = ("pick", "signal", "guarantee", "return", "profit", "beat", "advice", "recommend",
                   "fmp", "financial modeling prep", "download", "free", "now", "available", "get",
                   "powered", "invest", "buy", "sell", "stock", "price")


#: What the public-copy scan may find in each code-owned value line: the brand, and — on the PRE-ORDER
#: line only — its own App Store call to action. `compliance._APP_STORE_CTA_RE`'s availability shapes
#: (2026-10-07) exist to keep that claim out of MODEL text; this code-owned line is the one place
#: allowed to make it. Anything else (a pick, a return, a person) is a finding.
_VALUE_SCAN = {
    "prelaunch": [("brand_mention", "caydex")],
    "live": [("brand_mention", "caydex")],
    "preorder": [("brand_mention", "caydex"), ("brand_mention", " - pre-order on the app store")],
}


@pytest.mark.parametrize("state", STATES)
def test_every_value_line_passes_the_public_copy_scan(state):
    line = pc.value_line(state)
    # Strict: the brand (and the pre-order line's own store CTA) is all the scan finds, in every mode.
    assert [(v.code, v.detail) for v in scan_text("f", clean(line), allow_emoji=True)] == _VALUE_SCAN[state]
    assert [(v.code, v.detail) for v in scan_text("f", clean(line), allow_emoji=False,
                                                  strict_instruments=True)] == _VALUE_SCAN[state]
    assert [(v.code, v.detail) for v in scan_text("f", clean(line), allow_emoji=False,
                                                  strict_instruments=False)] == _VALUE_SCAN[state]
    assert clean(line) == line                       # what is stored is what was pinned
    assert not re.search(r"[0-9%$]", line)           # no number, no price, no percentage
    assert pc.x_link_tokens(line) == []              # no link X would weigh or bill
    assert not any(c in line for c in pc.FORBIDDEN_CHARS["youtube_title"])
    assert line.count("\n") == 0
    low = line.lower()
    for word in _VALUE_DENYLIST:
        assert not re.search(rf"(?<![a-z]){re.escape(word)}", low), (state, word)


@pytest.mark.parametrize("state", STATES)
def test_every_value_line_scans_clean_under_every_items_own_settings(state):
    """The line rides on EVERY item's captions, so it must pass the scan exactly as a body of that
    item would be scanned (`writer_service._scan`: the item's strict-instrument mode, its company
    terms and its fact-sheet words). The bare scan is not enough: a tier-2 word beside a name only
    an item's OWN terms know ("… on Kirkland, a discount brand …") scans clean there and is a
    class_b_evaluative verdict on the Costco, Apple and NVIDIA case studies' posts."""
    from app.services.marketing import content_pool

    line = clean(pc.value_line(state))
    items = [content_pool.get_item(k) for k in content_pool.eligible_keys()]
    # Anti-vacuity: both kinds, with their different strict modes, are really scanned.
    assert len(items) >= 30
    assert {i.kind for i in items} == {content_pool.MONEY_MOVES, content_pool.JOURNEY}
    assert any(i.company_terms for i in items)
    for item in items:
        got = scan_text("x", line, allow_emoji=True,
                        strict_instruments=content_pool.strict_instruments(item.kind),
                        company_terms=item.company_terms, sheet_words=item.grounding.tokens)
        assert [(v.code, v.detail) for v in got] == _VALUE_SCAN[state], (state, item.key)


def _parts(caption: str, body: str, state: str, disclaimer: str):
    line = pc.value_line(state)
    assert caption.startswith(body)
    return caption.index(line), len(body), caption.rindex(disclaimer)


@pytest.mark.parametrize("platform", pc.PLATFORMS)
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("allow_x_url", [False, True])
def test_every_caption_but_the_youtube_title_carries_the_value_line_once(platform, state, allow_x_url):
    post = pc.compose(platform, BODIES, category="blueprints", run_date=DATE,
                      allow_x_url=allow_x_url, store_state=state)
    field = _field(platform)
    disc = pc.disclaimer_for(field, DATE)
    assert post.caption.count(pc.VALUE_PRODUCT) == 1
    assert post.caption.count(pc.value_line(state)) == 1
    for other in STATES:   # the run's state is the one worded — never another state's claim
        if other != state:
            assert pc.value_line(other) not in post.caption, (state, other)
    at, body_end, disc_at = _parts(post.caption, BODIES[field], state, disc)
    assert body_end <= at < disc_at                  # after the body, before the disclaimer
    for tag in re.finditer(r"#\w+", post.caption):   # hashtags come before it
        assert tag.start() < at
    assert post.caption.endswith(disc) and post.caption.count(disc) == 1
    assert pc.check_composed(post, DATE) == []
    if platform == "youtube":
        assert pc.VALUE_PRODUCT not in (post.title or "")
        assert post.title == BODIES["youtube_title"]


@pytest.mark.parametrize("junk", [None, "", "invalid", "LIVE", " live", "preorder\n", "Preorder",
                                  "pre-order", 1, True, 0, b"live", ["live"], {"live": 1}, 1.5])
def test_an_unknown_store_state_words_as_prelaunch(junk):
    """Anything but the three exact names — wrong case, padding, a typo, a non-string, an
    unhashable value — is the PRELAUNCH line and the PRELAUNCH budget: the line that claims least.
    Never a raise (a list or dict must not reach a dict lookup)."""
    assert pc.normalize_store_state(junk) == pc.STORE_PRELAUNCH
    assert pc.value_line(junk) == _VALUE_GOLDEN["prelaunch"]
    for field in pc.CAPTION_FIELDS:
        for allow in (False, True):
            assert pc.cta_for(field, allow_x_url=allow, store_state=junk) == pc.cta_for(
                field, allow_x_url=allow, store_state=pc.STORE_PRELAUNCH), field
    for field in COMPUTED:
        assert pc.body_budget(field, "blueprints", DATE, store_state=junk) == pc.body_budget(
            field, "blueprints", DATE, store_state=pc.STORE_PRELAUNCH), field
    for platform in pc.PLATFORMS:
        assert pc.compose(platform, BODIES, category="blueprints", run_date=DATE, store_state=junk) == \
            pc.compose(platform, BODIES, category="blueprints", run_date=DATE)


#: body_budget(field, "blueprints", DATE) per state, computed by hand from the suffix:
#: X 280 − ("\n\n#businessstrategy" 19 + "\n\n" + line + "\n\n" + 60-char short disclaimer); the
#: em dash weighs 1 on X. Threads 500 − (19 + 2 + line + " Learn more: " 13 + 35-char link + 62).
#: Bluesky 300 − (2 + line + 13 + 35 + 62) — no hashtag. Lines: prelaunch 40, preorder 69, live 59.
#: (prelaunch: X 280 − 123 = 157; Threads 500 − 171 = 329; Bluesky 300 − 152 = 148.)
_BUDGET_GOLDEN = {
    "x": {"prelaunch": 157, "preorder": 128, "live": 138},
    "threads": {"prelaunch": 329, "preorder": 300, "live": 310},
    "bluesky": {"prelaunch": 148, "preorder": 119, "live": 129},
}


@pytest.mark.parametrize("field", sorted(_BUDGET_GOLDEN))
@pytest.mark.parametrize("state", STATES)
def test_the_value_line_costs_the_computed_budgets(field, state):
    """Any wording change that eats a computed budget has to be a deliberate edit here."""
    assert len(_VALUE_GOLDEN[state]) == {"prelaunch": 40, "preorder": 69, "live": 59}[state]
    # Every character of the line weighs 1 on X — the em dash (U+2014) of the store lines included —
    # so X spends exactly its length (a CJK character or an emoji would weigh 2 and shrink the budget).
    assert ("—" in _VALUE_GOLDEN[state]) is (state != pc.STORE_PRELAUNCH) and pc._x_char_weight("—") == 1
    assert pc.x_weighted_length(_VALUE_GOLDEN[state]) == len(_VALUE_GOLDEN[state])
    assert pc.body_budget(field, "blueprints", DATE, store_state=state) == _BUDGET_GOLDEN[field][state]
    with_url = pc.body_budget(field, "blueprints", DATE, allow_x_url=True, store_state=state)
    # The X link adds " " + a 23-weight URL; nothing else changes, on X or anywhere else.
    assert with_url == _BUDGET_GOLDEN[field][state] - (24 if field == "x" else 0)


# ── carries_go_link: the captions that carry their OWN /go link ───────────────


@pytest.mark.parametrize("allow_x_url", [False, True])
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("category", ["blueprints", "foundation", "unknown-category"])
def test_the_link_bearing_platforms_are_exactly_those_whose_caption_carries_its_go_link(
        allow_x_url, state, category):
    got = {p for p in pc.PLATFORMS
           if pc.carries_go_link(p, pc.compose(p, BODIES, category=category, run_date=DATE,
                                               allow_x_url=allow_x_url, store_state=state).caption)}
    assert got == {"bluesky", "facebook", "linkedin", "threads", "youtube"} | ({"x"} if allow_x_url else set())


@pytest.mark.parametrize("platform", [p for p in pc.PLATFORMS if p not in ("tiktok", "instagram")])
def test_go_link_is_the_tail_of_its_cta(platform):
    cta = pc.cta_for(_field(platform), allow_x_url=True)
    assert pc.go_link(platform) == f"https://caydexinvest.com/go/{platform}"
    assert cta.endswith(" " + pc.go_link(platform))


_X = "https://caydexinvest.com/go/x"


@pytest.mark.parametrize("platform, caption, expected", [
    ("x", f"Body.\n\n{_X}", True),                      # at the very end
    ("x", f"{_X}.", True),                              # a sentence's full stop
    ("x", f"{_X}\nmore", True),                         # a line break
    ("x", f"{_X} more", True),                          # a space
    ("x", f"see {_X}yz", False),                        # /go/xyz is another slug
    ("x", f"{_X}_early", False),                        # /go/x_early is another slug
    ("x", f"{_X}-2", False),
    ("x", f"{_X}9", False),
    ("x", f"{_X}Y", False),
    ("x", f"{_X}yz then {_X}.", True),                  # a later exact link still counts
    ("x", "http://caydexinvest.com/go/x", False),       # not the spelling cta_for writes
    ("x", "https://caydexinvest.com/go/X", False),      # another (case-different) slug
    ("x", "https://caydexinvest.com/go/bluesky", False),  # another platform's link
    ("threads", "Learn more: https://caydexinvest.com/go/bluesky", False),
    ("bluesky", "Learn more: https://caydexinvest.com/go/bluesky", True),
    ("youtube", "Learn more: https://caydexinvest.com/go/youtube", True),
    ("tiktok", f"{_VALUE_GOLDEN['live']} Link in bio.", False),
    ("x", "", False),
    ("x", None, False),
    ("x", 123, False),
    ("x", [_X], False),
    ("", _X, False),
    # An empty platform is never a campaign, even where an empty slug WOULD match: the bare /go/
    # prefix followed by nothing, a query or a space (go_link('') is exactly that prefix).
    ("", "https://caydexinvest.com/go/", False),
    ("", "Learn more: https://caydexinvest.com/go/?ct=x", False),
    ("", "https://caydexinvest.com/go/ then text", False),
    (None, _X, False),
    (5, _X, False),
])
def test_carries_go_link_reads_only_the_platforms_own_link(platform, caption, expected):
    assert pc.carries_go_link(platform, caption) is expected


#: The characters that continue a /go slug, as the /go design states the rule: the caption carries
#: go_link(platform) NOT followed by an ASCII letter, digit, '_' or '-'.
_SLUG_CONTINUES = frozenset(string.ascii_letters + string.digits + "_-")


@pytest.mark.parametrize("platform", ["x", "bluesky", "youtube"])
def test_carries_go_link_ends_the_slug_at_every_ascii_character_a_slug_cannot_hold(platform):
    """Every one of the 128 ASCII characters, glued after the link at the end of a caption and in
    the middle of one: exactly the slug characters make it another slug ('/go/xyz', '/go/x_early',
    '/go/x-2', '/go/X9'); everything else — '.', ',', ')', '?', '/', '#', a space, a line break, a
    control character — ends the slug, so the link counts."""
    link = pc.go_link(platform)
    assert len(_SLUG_CONTINUES) == 64      # 52 letters, 10 digits, '_' and '-'
    for code in range(128):
        ch = chr(code)
        expected = ch not in _SLUG_CONTINUES
        assert pc.carries_go_link(platform, link + ch) is expected, (platform, repr(ch))
        assert pc.carries_go_link(platform, f"Body.\n\n{link}{ch} tail") is expected, (platform, repr(ch))
        # A glued slug character does not hide an exact link later in the same caption.
        assert pc.carries_go_link(platform, f"{link}{ch} then {link}") is True, (platform, repr(ch))


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
@pytest.mark.parametrize("state", STATES)
def test_a_body_of_exactly_the_budget_fits(field, category, allow_x_url, state):
    budget = pc.body_budget(field, category, DATE, allow_x_url=allow_x_url, store_state=state)
    assert budget >= pc._MIN_BODY
    bodies = dict(BODIES, **{field: _body_of_length(field, budget)})
    post = pc.compose(field, bodies, category=category, run_date=DATE, allow_x_url=allow_x_url,
                      store_state=state)
    assert pc.measured_length(field, post.caption) <= pc.LIMITS[field]
    assert pc.check_composed(post, DATE) == []


@pytest.mark.parametrize("field", COMPUTED)
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("allow_x_url", [False, True])
def test_budget_is_tight(field, state, allow_x_url):
    budget = pc.body_budget(field, "blueprints", DATE, allow_x_url=allow_x_url, store_state=state)
    bodies = dict(BODIES, **{field: _body_of_length(field, budget + 1)})
    post = pc.compose(field, bodies, category="blueprints", run_date=DATE, allow_x_url=allow_x_url,
                      store_state=state)
    assert pc.measured_length(field, post.caption) == pc.LIMITS[field] + 1


@pytest.mark.parametrize("field", COMPUTED)
def test_the_budget_is_the_one_for_the_state_the_caption_is_composed_with(field):
    """A body sized to one state's budget fits exactly the states whose line is no longer — it
    overflows when composed with a longer line, so the prompt and the validator must use the run's
    own state, never a default. Since 2026-10-07 the claim-free prelaunch line is the shortest."""
    budgets = {s: pc.body_budget(field, "blueprints", DATE, store_state=s) for s in STATES}
    assert budgets[pc.STORE_PREORDER] < budgets[pc.STORE_LIVE] < budgets[pc.STORE_PRELAUNCH], budgets
    for sized in STATES:
        body = _body_of_length(field, budgets[sized])
        for state in STATES:
            post = pc.compose(field, dict(BODIES, **{field: body}), category="blueprints", run_date=DATE,
                              store_state=state)
            assert (pc.check_composed(post, DATE) == []) is (budgets[state] >= budgets[sized]), (field, sized, state)


@pytest.mark.parametrize("unit", ["\u65e5", "\U0001F680", "\u00e9"])
@pytest.mark.parametrize("allow_x_url", [False, True])
@pytest.mark.parametrize("state", STATES)
def test_x_budget_holds_with_weighted_characters(unit, allow_x_url, state):
    budget = pc.body_budget("x", "blueprints", DATE, allow_x_url=allow_x_url, store_state=state)
    body = _body_of_length("x", budget, unit)
    post = pc.compose("x", dict(BODIES, x=body), category="blueprints", run_date=DATE,
                      allow_x_url=allow_x_url, store_state=state)
    assert pc.x_weighted_length(post.caption) <= 280
    assert pc.check_composed(post, DATE) == []
    over = pc.compose("x", dict(BODIES, x=body + "\u65e5" * 10), category="blueprints",
                      run_date=DATE, allow_x_url=allow_x_url, store_state=state)
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
@pytest.mark.parametrize("state", STATES)
def test_the_code_owned_suffix_never_contains_a_refused_character(category, allow_x_url, state):
    for field, bad in pc.FORBIDDEN_CHARS.items():
        suffix = pc._suffix(field, category, DATE, allow_x_url, state)
        assert not any(c in suffix for c in bad), (field, suffix)
    assert _forbidden(_yt()) == []
    post = pc.compose("youtube", BODIES, category=category, run_date=DATE, allow_x_url=allow_x_url,
                      store_state=state)
    assert _forbidden(post) == []


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
@pytest.mark.parametrize("state", STATES)
def test_every_budget_plus_its_suffix_fits_the_platform_and_never_hits_the_floor(allow_x_url, state):
    """A body at its budget always composes within the platform limit, and no computed budget is
    clamped up to _MIN_BODY (a clamp would make check_composed's length arm load-bearing — and
    silently drop that outlet every day). Fails the moment a suffix grows too long — the value line
    made it longer in every state (worst: preorder + X URLs on, 102 for mastery, 22 above the floor),
    and the hostile / unknown categories carry their own fallback hashtag."""
    categories = list(dict.fromkeys(list(_eligible_categories()) + list(CATEGORIES) + list(HOSTILE_CATEGORIES)))
    for category in categories:
        for field in pc.CAPTION_FIELDS:
            suffix = pc._suffix(field, category, DATE, allow_x_url, state)
            budget = pc.body_budget(field, category, DATE, allow_x_url=allow_x_url, store_state=state)
            assert budget + pc.measured_length(field, suffix) <= pc.LIMITS[field], (field, category)
            if field in COMPUTED:
                assert pc.LIMITS[field] - pc.measured_length(field, suffix) > pc._MIN_BODY, (field, category)


def test_the_tightest_computed_budget_is_the_measured_one():
    """Pins the worst margin the design measured: preorder (the longest line) with X URLs on, in the
    category with the longest hashtag (mastery) — 102, still 22 above _MIN_BODY."""
    worst = min(pc.body_budget(f, c, DATE, allow_x_url=a, store_state=s)
                for f in COMPUTED for c in _eligible_categories() for a in (False, True) for s in STATES)
    assert worst == 102 == pc.body_budget("x", "mastery", DATE, allow_x_url=True, store_state=pc.STORE_PREORDER)
    assert worst - pc._MIN_BODY == 22


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


# ── x_link_tokens: ONE definition of a link for the length counter and the publish guard ──────
# (Phase 5: X charges $0.20 instead of $0.015 for a post with a URL, so the publisher refuses any
# link token before the claim; the counter weighs each one at 23. If the two ever disagreed, a
# caption could pass the length check while carrying a link the guard misses, or vice versa.)

_LINK_TABLE = [
    ("", []),
    ("plain words", []),
    ("U.S.dollar", []), ("e.g.the", []), ("vs.the", []), ("U.S.Economy", []), ("e.g.,the", []),
    ("U.S. e.g. i.e. 3.14 v2.0", []),
    ("AI-assisted. Caydex", []),
    ("Learn.Money", ["Learn.Money"]),
    ("Learn.Money.", ["Learn.Money"]),
    ("investor.gov", ["investor.gov"]),
    ("investor.gov.", ["investor.gov"]),
    ("see investor.gov.", ["investor.gov"]),
    ("caydex.com", ["caydex.com"]), ("CAYDEX.COM", ["CAYDEX.COM"]), ("www.caydex.com", ["www.caydex.com"]),
    ("U.S.markets", ["U.S.markets"]), ("e.g.bank", ["e.g.bank"]),
    ("https://caydexinvest.com/go/x", ["https://caydexinvest.com/go/x"]),
    ("http://a.co", ["http://a.co"]),
    ("https://", ["https://"]),
    ("abc\nhttps://caydexinvest.com/go/x", ["https://caydexinvest.com/go/x"]),
    ("https://caydexinvest.com/go/x\nabc", ["https://caydexinvest.com/go/x"]),
    ("https://a.co tail", ["https://a.co"]),
    # a scheme glued to text is still a link (the domain inside it)
    ("readhttps://caydex.com", ["caydex.com"]),
    ("see:https://caydex.com/x", ["caydex.com"]),
    ("(https://caydex.com)", ["caydex.com"]),
    ("two: a.co and https://b.co/x", ["a.co", "https://b.co/x"]),
    ("U.S.dollar e.g.the investor.gov", ["investor.gov"]),
    ("日本 investor.gov \U0001F4C8", ["investor.gov"]),
    ("a.co b.co", ["a.co", "b.co"]),
]


def _weight(text: str) -> int:
    return sum(pc._x_char_weight(c) for c in text)


@pytest.mark.parametrize("text, links", _LINK_TABLE)
def test_x_link_tokens_finds_exactly_the_links(text, links):
    assert pc.x_link_tokens(text) == links


@pytest.mark.parametrize("text", [t for t, _ in _LINK_TABLE] + list(BODIES.values()) + [None])
def test_x_link_tokens_agrees_with_the_weighted_length(text):
    """x_weighted_length == every character's weight, minus each link's characters, plus 23 a link —
    exactly when the guard and the counter agree on WHERE every link is."""
    links = pc.x_link_tokens(text)
    rest = text or ""
    pos = 0
    for tok in links:   # each token is a substring, in order, never overlapping the previous one
        i = rest.find(tok, pos)
        assert i >= 0, (text, tok)
        pos = i + len(tok)
    expected = _weight(text or "") - sum(_weight(t) for t in links) + pc._X_URL_WEIGHT * len(links)
    assert pc.x_weighted_length(text) == expected


def test_x_link_tokens_agrees_with_the_counter_on_every_real_draft_and_composed_caption():
    corpus = json.loads((BACKEND / "tests" / "data" / "marketing_real_drafts_2026_09_24.json").read_text())
    texts = [row[2] for row in corpus["honest"] if isinstance(row, list) and len(row) > 2]
    assert len(texts) > 1000   # anti-vacuity: the corpus is really read
    for allow in (False, True):
        for platform in COMPUTED:
            for state in STATES:
                texts.append(pc.compose(platform, BODIES, category="mastery", run_date=DATE,
                                        allow_x_url=allow, store_state=state).caption)
    for text in texts:
        links = pc.x_link_tokens(text)
        expected = _weight(text) - sum(_weight(t) for t in links) + pc._X_URL_WEIGHT * len(links)
        assert pc.x_weighted_length(text) == expected, text


def _real_bodies(field: str):
    corpus = json.loads((BACKEND / "tests" / "data" / "marketing_real_drafts_2026_09_24.json").read_text())
    bodies = [row[2] for row in corpus["honest"] if isinstance(row, list) and len(row) > 2 and row[1] == field]
    assert len(bodies) >= 20, field   # anti-vacuity: the real X / Bluesky bodies are really there
    return bodies


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("state", STATES)
def test_the_composed_x_caption_carries_no_link(category, state):
    """allow_x_url=False is the shipped setting: no CTA link, so nothing the publisher's URL guard
    would refuse comes from the code-owned suffix (value line included), and no real body adds one."""
    for body in _real_bodies("x"):
        post = pc.compose("x", {**BODIES, "x": body}, category=category, run_date=DATE, store_state=state)
        assert pc.x_link_tokens(post.caption) == [], post.caption
        assert "caydexinvest.com" not in post.caption
        assert not pc.carries_go_link("x", post.caption)


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("state", STATES)
def test_the_composed_bluesky_caption_has_exactly_one_go_link_and_no_hashtag(category, state):
    for body in _real_bodies("bluesky"):
        post = pc.compose("bluesky", {**BODIES, "bluesky": body}, category=category, run_date=DATE,
                          store_state=state)
        assert pc.x_link_tokens(post.caption) == [f"{pc.LINK_BASE_URL}/bluesky"], post.caption
        assert post.caption.count("https://") == 1 and post.caption.count("/go/") == 1
        assert not re.search(r"(?<!\S)#\w", post.caption), post.caption
        assert pc.carries_go_link("bluesky", post.caption)


# ── the post image's footer (drop 1, 2026-10-09) ──────────────────────────────
#
# Code-owned copy burned into every post image (`pc.image_footer`), required by the server among the
# image's declared on-screen text. Nothing scans it at runtime — like the value line, these tests are
# its only guard: pinned verbatim, and scanned exactly like the caption disclaimers it stands beside.

_FOOTER_GOLDEN = {
    date(2026, 10, 9): ("Educational only · not investment advice · Written with AI assistance · "
                        "Oct 9, 2026 · Caydex"),
    date(2027, 1, 5): ("Educational only · not investment advice · Written with AI assistance · "
                       "Jan 5, 2027 · Caydex"),
    date(2028, 2, 29): ("Educational only · not investment advice · Written with AI assistance · "
                        "Feb 29, 2028 · Caydex"),
}


@pytest.mark.parametrize("run_date", sorted(_FOOTER_GOLDEN))
def test_the_image_footer_is_pinned_verbatim(run_date):
    assert pc.image_footer(run_date) == _FOOTER_GOLDEN[run_date]
    assert pc.image_footer(run_date, "ai") == _FOOTER_GOLDEN[run_date]
    assert pc.image_footer(run_date, authorship="ai") == _FOOTER_GOLDEN[run_date]


def test_the_image_footer_discloses_ai_and_claims_no_narration():
    """An AI-written lesson image says so; nothing in an image is narrated (the video's own
    disclaimer card says "Script and narration generated with AI" — never the image)."""
    footer = pc.image_footer(DATE)
    low = footer.lower()
    assert "Written with AI assistance" in footer and re.search(r"\bAI\b", footer)
    assert "narrat" not in low and "voice" not in low
    assert "not investment advice" in low and "educational" in low
    assert footer.endswith(f"· {pc.PUBLISHER}") and pc.PUBLISHER == "Caydex"
    assert not re.search(r"caydex,?\s+inc", low)
    for banned in ("financial advisor", "financial adviser", "investment advisor", "investment adviser"):
        assert banned not in low


@pytest.mark.parametrize("authorship", ["template", "AI", "Ai", "", "human", None, 1, "ai "])
def test_the_image_footer_refuses_any_authorship_but_ai(authorship):
    """`template` (drop 2) words a footer only with its required `source` and `as_of` (tests at the
    end of this file), so a bare "template" call still raises; anything else is a typo — never a
    footer that drops (or invents) the AI line."""
    with pytest.raises(ValueError):
        pc.image_footer(DATE, authorship)


#: What the public-copy scan finds in the footer — exactly what it finds in the short caption
#: disclaimer: the disclaimer's own "investment advice", the brand, and nothing else.
_FOOTER_SCAN = [("banned_phrase", "investment advice"), ("brand_mention", "caydex"), ("code_owned", "advice")]


@pytest.mark.parametrize("run_date", sorted(_FOOTER_GOLDEN))
def test_the_image_footer_passes_the_public_copy_scan(run_date):
    from app.schemas.marketing import ONSCREEN_TEXT_MAX_CHARS

    footer = pc.image_footer(run_date)
    for kw in ({"allow_emoji": True}, {"allow_emoji": False, "strict_instruments": True},
               {"allow_emoji": False, "strict_instruments": False}):
        assert [(v.code, v.detail) for v in scan_text("f", clean(footer), **kw)] == _FOOTER_SCAN, kw
    # Anti-vacuity: the scan reads the caption disclaimer the same way.
    assert [(v.code, v.detail) for v in scan_text("f", clean(pc.disclaimer_short()))] == _FOOTER_SCAN
    assert clean(footer) == footer                    # what is stored is what was pinned
    assert not re.search(r"[%$]", footer)             # no price, no percentage
    assert pc.x_link_tokens(footer) == []             # no link
    assert "\n" not in footer and footer.count("·") == 4
    assert len(footer) <= ONSCREEN_TEXT_MAX_CHARS     # declarable as on-screen text
    low = footer.lower()
    for word in ("pick", "signal", "guarantee", "return", "profit", "beat", "recommend", "fmp",
                 "download", "free", "buy", "sell", "stock", "price", "app store"):
        assert not re.search(rf"(?<![a-z]){re.escape(word)}", low), word


def test_the_image_footer_scans_like_a_disclaimer_under_every_items_own_settings():
    """It rides on every lesson's image, so it is scanned as a body of that item would be."""
    from app.services.marketing import content_pool

    footer = clean(pc.image_footer(DATE))
    items = [content_pool.get_item(k) for k in content_pool.eligible_keys()]
    assert len(items) >= 30 and any(i.company_terms for i in items)
    for item in items:
        got = scan_text("x", footer, allow_emoji=True,
                        strict_instruments=content_pool.strict_instruments(item.kind),
                        company_terms=item.company_terms, sheet_words=item.grounding.tokens)
        assert [(v.code, v.detail) for v in got] == _FOOTER_SCAN, item.key


# ── authorship (drop 2, contract D8; owner decision 4 of 2026-10-09) ─────────────────────────────
#
# "ai" is the class-A lesson writer — every call above uses the default. "template" is the
# code-owned news templates of classes C/F: their copy says a fixed template built it from public
# data and never claims an AI wrote it; a narrated video discloses its AI voice; the non-affiliation
# line rides on every template disclaimer, card and footer. Nothing scans this copy at runtime, so
# these tests are its only guard.

import hashlib  # noqa: E402

from app.services.trillion_club.copy_rules import contains_banned_copy, contains_forecast  # noqa: E402

TEMPLATE = "template"
NEWS_CATEGORIES = ("news:ceo_buys", "news:insider_buys", "news:thirteen_f", "news:congress_count",
                   "news:company_stakes", "news:earnings", "news:money_map", "news:theme_explainer")
JUNK_AUTHORSHIPS = ("AI", "Template", "template ", " ai", "", "human", "writer", None, 1, True,
                    ["ai"], ("template",), b"ai")


def _ai_outputs(**kw):
    """Every output the module produces for an "ai" lesson, in a fixed order (`kw` is {} or
    {"authorship": "ai"}): disclaimers, footer, hashtags, budgets, suffixes, composed captions and
    their violations (clean and broken bodies, the right and a wrong date)."""
    rows = []
    for d in (date(2026, 9, 23), date(2027, 1, 5), date(2028, 2, 29)):
        rows.append(["long", str(d), pc.disclaimer_long(d, **kw)])
        rows.append(["long_video", str(d), pc.disclaimer_long(d, video=True, **kw)])
        rows.append(["card", str(d), pc.disclaimer_card(d, **kw)])
        rows.append(["footer", str(d), pc.image_footer(d, **kw)])
        for field in pc.CAPTION_FIELDS:
            rows.append(["disclaimer_for", field, str(d), pc.disclaimer_for(field, d, **kw)])
    rows.append(["short", pc.disclaimer_short(**kw)])
    bad_bodies = dict(BODIES, youtube_title="Price > value\nnow", x="a" * 300,
                      youtube_description="Patience > timing.")
    for category in CATEGORIES + HOSTILE_CATEGORIES:
        for field in pc.CAPTION_FIELDS:
            rows.append(["tags", category, field, pc.hashtags_for(field, category)])
        for state in STATES + ("junk",):
            for allow in (False, True):
                for field in pc.CAPTION_FIELDS:
                    rows.append(["budget", category, state, allow, field,
                                 pc.body_budget(field, category, DATE, allow_x_url=allow,
                                                store_state=state, **kw)])
                    rows.append(["suffix", category, state, allow, field,
                                 pc._suffix(field, category, DATE, allow, state, **kw)])
                for platform in pc.PLATFORMS:
                    for bodies in (BODIES, bad_bodies):
                        post = pc.compose(platform, bodies, category=category, run_date=DATE,
                                          allow_x_url=allow, store_state=state, **kw)
                        for checked_on in (DATE, date(2026, 9, 24)):
                            rows.append(["compose", category, state, allow, platform, post.as_dict(),
                                         [v.as_dict() for v in pc.check_composed(post, checked_on, **kw)]])
    return rows


def _ai_digest(**kw) -> str:
    blob = json.dumps(_ai_outputs(**kw), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


#: sha256 of `_ai_outputs()` computed on the drop-1 module BEFORE the authorship keyword existed
#: (2026-10-09). Any change to an ai-authored output — a disclaimer, the footer, a suffix, a budget,
#: a composed caption or a violation — fails here: Learn's copy stays byte for byte what it was.
_AI_DIGEST_BEFORE_AUTHORSHIP = "2300ab6a9d9db454f974f9189e0c9536225fcafa5d3cf18c1dd0aabec2be794e"


def test_every_ai_output_is_byte_identical_to_before_authorship():
    assert len(_ai_outputs()) == 6584   # anti-vacuity: the whole matrix is really walked
    assert _ai_digest() == _AI_DIGEST_BEFORE_AUTHORSHIP
    assert _ai_digest(authorship="ai") == _AI_DIGEST_BEFORE_AUTHORSHIP


def test_the_authorship_names_and_lines_are_pinned():
    assert pc.AUTHORSHIPS == ("ai", "template")
    assert (pc.AUTHORSHIP_AI, pc.AUTHORSHIP_TEMPLATE) == pc.AUTHORSHIPS
    assert pc.IMAGE_AUTHORSHIPS == pc.AUTHORSHIPS
    assert pc.NON_AFFILIATION == "Not affiliated with anyone named."
    assert pc.TEMPLATE_NOTE_TEXT == "Built by a fixed template from public data."
    assert pc.TEMPLATE_NOTE_VIDEO == "Built by a fixed template from public data; narration voiced with AI."
    assert pc.AI_DISCLOSURES == ("Written with AI assistance", "AI-assisted",
                                 "Script and narration generated with AI")


def test_the_template_authorship_name_matches_the_schema():
    from app.schemas import marketing as sch

    if not hasattr(sch, "TEMPLATE_AUTHORSHIP"):
        pytest.skip("schemas.marketing.TEMPLATE_AUTHORSHIP (contract D2) is not declared yet")
    assert sch.TEMPLATE_AUTHORSHIP == pc.AUTHORSHIP_TEMPLATE


def _template_disclaimers(run_date: date):
    return {
        "long": pc.disclaimer_long(run_date, authorship=TEMPLATE),
        "long_video": pc.disclaimer_long(run_date, video=True, authorship=TEMPLATE),
        "short": pc.disclaimer_short(TEMPLATE),
        "card": pc.disclaimer_card(run_date, TEMPLATE),
    }


_TEMPLATE_GOLDEN = {
    "long": ("Caydex · Educational, impersonal information — not investment advice, not a "
             "recommendation, not an offer. Investing involves risk, including loss of principal. "
             "Built by a fixed template from public data. Not affiliated with anyone named. "
             "Sep 23, 2026."),
    "long_video": ("Caydex · Educational, impersonal information — not investment advice, not a "
                   "recommendation, not an offer. Investing involves risk, including loss of "
                   "principal. Built by a fixed template from public data; narration voiced with AI. "
                   "Not affiliated with anyone named. Sep 23, 2026."),
    "short": "Educational only, not investment advice. Not affiliated with anyone named. Caydex",
    "card": ("Educational, impersonal information — not investment advice. Investing involves "
             "risk. Built by a fixed template from public data; narration voiced with AI. Not "
             "affiliated with anyone named. Caydex · Sep 23, 2026"),
}


@pytest.mark.parametrize("variant", sorted(_TEMPLATE_GOLDEN))
def test_the_template_disclaimers_are_pinned_verbatim(variant):
    assert _template_disclaimers(DATE)[variant] == _TEMPLATE_GOLDEN[variant]
    # Keyword and positional spellings agree (callers use both).
    assert pc.disclaimer_short(authorship=TEMPLATE) == _TEMPLATE_GOLDEN["short"]
    assert pc.disclaimer_card(DATE, authorship=TEMPLATE) == _TEMPLATE_GOLDEN["card"]


@pytest.mark.parametrize("variant", sorted(_TEMPLATE_GOLDEN))
def test_template_copy_never_claims_an_ai_wrote_it(variant):
    text = _template_disclaimers(DATE)[variant]
    for line in pc.AI_DISCLOSURES:
        assert line.casefold() not in text.casefold(), (variant, line)
    assert "generated with ai" not in text.lower() and "ai assistance" not in text.lower()
    assert text.count(pc.NON_AFFILIATION) == 1
    assert pc.PUBLISHER in text and "not investment advice" in text
    if variant in ("long_video", "card"):   # a narrated video says its voice is AI, once
        assert text.count("narration voiced with AI") == 1
        assert len(re.findall(r"\bAI\b", text)) == 1
    else:                                    # a template's text/image copy has no AI in it at all
        assert not re.search(r"\bAI\b", text)
        assert "narrat" not in text.lower() and "voice" not in text.lower()
    if variant != "short":                   # the long forms keep the ai forms' legal sentences
        assert "Investing involves risk" in text
        assert "Sep 23, 2026" in text
    if variant.startswith("long"):
        assert "not a recommendation, not an offer" in text


@pytest.mark.parametrize("variant", sorted(_TEMPLATE_GOLDEN))
def test_the_template_disclaimers_keep_the_ai_forms_legal_text(variant):
    """The template forms differ from the ai forms ONLY in the AI line (and the non-affiliation
    line beside it): the legal sentences around it are the same characters."""
    ai = _all_disclaimers(DATE)[variant]
    tpl = _template_disclaimers(DATE)[variant]
    old = {"long": "Written with AI assistance.", "long_video": "Script and narration generated with AI.",
           "short": "AI-assisted.", "card": "Script and narration generated with AI."}[variant]
    new = {"long": f"{pc.TEMPLATE_NOTE_TEXT} {pc.NON_AFFILIATION}",
           "long_video": f"{pc.TEMPLATE_NOTE_VIDEO} {pc.NON_AFFILIATION}",
           "short": pc.NON_AFFILIATION,
           "card": f"{pc.TEMPLATE_NOTE_VIDEO} {pc.NON_AFFILIATION}"}[variant]
    assert ai.count(old) == 1
    assert ai.replace(old, new) == tpl


#: What the public-copy scan finds in the long disclaimers — template and ai alike: the disclaimer's
#: own legal phrases and the brand, nothing else.
_LONG_SCAN = [("banned_phrase", "investment advice"), ("banned_phrase", "not a recommendation"),
              ("brand_mention", "caydex"), ("code_owned", "advice"), ("code_owned", "not a recommendation")]


@pytest.mark.parametrize("variant", sorted(_TEMPLATE_GOLDEN))
def test_the_template_disclaimers_pass_the_public_copy_scan(variant):
    text = _template_disclaimers(DATE)[variant]
    want = _LONG_SCAN if variant.startswith("long") else _FOOTER_SCAN
    for kw in ({"allow_emoji": True}, {"allow_emoji": False, "strict_instruments": True},
               {"allow_emoji": False, "strict_instruments": False}):
        assert [(v.code, v.detail) for v in scan_text("f", clean(text), **kw)] == want, kw
        # Anti-vacuity: the ai form of the same variant scans the same way.
        assert [(v.code, v.detail) for v in scan_text("f", clean(_all_disclaimers(DATE)[variant]), **kw)] == want
    assert clean(text) == text
    assert "\n" not in text
    assert pc.x_link_tokens(text) == []
    assert not re.search(r"[%$]", text)
    assert not re.search(r"caydex,?\s+inc", text.lower())


def _template_copy_strings():
    """Every code-owned string a template post can carry: the disclaimers, card, footer, value lines,
    CTAs and the news hashtags."""
    out = list(_template_disclaimers(DATE).values())
    out.append(pc.image_footer(DATE, TEMPLATE, source="SEC Form 4 filings", as_of="Filed Oct 5–9, 2026"))
    out += [pc.value_line(s) for s in STATES]
    out += [c for f in pc.CAPTION_FIELDS for s in STATES for a in (False, True)
            if (c := pc.cta_for(f, allow_x_url=a, store_state=s))]
    out += [pc._CATEGORY_TAG[c] for c in NEWS_CATEGORIES]
    return out


def test_template_copy_passes_the_news_templates_runtime_word_rules():
    """`news_templates` scans every output string with `copy_rules.BANNED_COPY` + `FORECAST_COPY`
    (contract D7 A8), the code-owned suffix included: none of it may trip either."""
    strings = _template_copy_strings()
    assert len(strings) > 20   # anti-vacuity
    for s in strings:
        assert not contains_banned_copy(s), s
        assert not contains_forecast(s), s


@pytest.mark.parametrize("field", pc.CAPTION_FIELDS)
def test_disclaimer_for_words_the_authorship(field):
    got = pc.disclaimer_for(field, DATE, TEMPLATE)
    assert got == pc.disclaimer_for(field, DATE, authorship=TEMPLATE)
    if field == "youtube_title":
        assert got is None
    elif field in COMPUTED:
        assert got == _TEMPLATE_GOLDEN["short"]
    elif field in VIDEO_FIELDS:
        assert got == _TEMPLATE_GOLDEN["long_video"]
    else:
        assert got == _TEMPLATE_GOLDEN["long"]


@pytest.mark.parametrize("platform", pc.PLATFORMS)
@pytest.mark.parametrize("category", NEWS_CATEGORIES)
@pytest.mark.parametrize("allow_x_url", [False, True])
@pytest.mark.parametrize("state", STATES)
def test_a_template_caption_ends_with_the_template_disclaimer_once(platform, category, allow_x_url, state):
    post = pc.compose(platform, BODIES, category=category, run_date=DATE, allow_x_url=allow_x_url,
                      store_state=state, authorship=TEMPLATE)
    field = _field(platform)
    disc = pc.disclaimer_for(field, DATE, TEMPLATE)
    assert post.caption.startswith(BODIES[field])                 # never sliced
    assert post.caption.endswith(disc) and post.caption.count(disc) == 1
    assert post.caption.count(pc.value_line(state)) == 1
    assert pc.check_composed(post, DATE, TEMPLATE) == []
    assert pc.check_composed(post, DATE, authorship=TEMPLATE) == []
    for line in pc.AI_DISCLOSURES:
        assert line not in post.caption
    # Checked as an ai caption it is missing the ai disclaimer: the two can never be confused.
    assert "disclaimer_missing" in [v.code for v in pc.check_composed(post, DATE)]
    # And an ai caption checked as a template one is refused the same way.
    ai_post = pc.compose(platform, BODIES, category=category, run_date=DATE, allow_x_url=allow_x_url,
                         store_state=state)
    assert "disclaimer_missing" in [v.code for v in pc.check_composed(ai_post, DATE, TEMPLATE)]
    tags = re.findall(r"#\w+", post.caption)
    assert tags == pc.hashtags_for(field, category)
    if tags:
        assert tags[0] == pc._CATEGORY_TAG[category]


@pytest.mark.parametrize("line", pc.AI_DISCLOSURES + ("written with ai assistance", "AI-ASSISTED"))
@pytest.mark.parametrize("platform", ["x", "facebook", "youtube"])
def test_a_template_post_that_says_an_ai_wrote_it_is_an_authorship_mismatch(line, platform):
    field = _field(platform)
    bodies = dict(BODIES, **{field: f"Three CEOs disclosed purchases. {line}."})
    post = pc.compose(platform, bodies, category="news:ceo_buys", run_date=DATE, authorship=TEMPLATE)
    got = [(v.field, v.code) for v in pc.check_composed(post, DATE, TEMPLATE)]
    assert got == [(platform, "authorship_mismatch")]
    if platform == "youtube":   # the title is read too
        post = pc.compose("youtube", dict(BODIES, youtube_title=f"CEO purchases ({line})"),
                          category="news:ceo_buys", run_date=DATE, authorship=TEMPLATE)
        assert [(v.field, v.code) for v in pc.check_composed(post, DATE, TEMPLATE)] == \
            [("youtube", "authorship_mismatch")]
    # The ai check is untouched: a lesson body may mention AI (its own disclaimer says so anyway).
    ai_post = pc.compose(platform, bodies, category="blueprints", run_date=DATE)
    assert "authorship_mismatch" not in [v.code for v in pc.check_composed(ai_post, DATE)]


#: body_budget(field, "news:ceo_buys", DATE, authorship="template") per state (allow_x_url off), by
#: hand: the template short disclaimer is 81 characters (the ai one 60), the tag "#secfilings" 11.
#: X 280 − ("\n\n#secfilings" 13 + "\n\n" + line + "\n\n" 2 + 81) → live 123, prelaunch 142, preorder 113.
_TEMPLATE_BUDGET_GOLDEN = {
    "x": {"prelaunch": 142, "preorder": 113, "live": 123},
    "threads": {"prelaunch": 314, "preorder": 285, "live": 295},
    "bluesky": {"prelaunch": 127, "preorder": 98, "live": 108},
}


@pytest.mark.parametrize("field", sorted(_TEMPLATE_BUDGET_GOLDEN))
@pytest.mark.parametrize("state", STATES)
def test_the_template_computed_budgets_are_pinned(field, state):
    assert len(pc.disclaimer_short(TEMPLATE)) == 81
    assert pc.body_budget(field, "news:ceo_buys", DATE, store_state=state, authorship=TEMPLATE) == \
        _TEMPLATE_BUDGET_GOLDEN[field][state]
    # A body of exactly the budget fits; one more character does not.
    budget = _TEMPLATE_BUDGET_GOLDEN[field][state]
    for n, ok in ((budget, True), (budget + 1, False)):
        post = pc.compose(field, {**BODIES, field: _body_of_length(field, n)}, category="news:ceo_buys",
                          run_date=DATE, store_state=state, authorship=TEMPLATE)
        assert (pc.check_composed(post, DATE, TEMPLATE) == []) is ok, (field, state, n)


@pytest.mark.parametrize("field", pc.CAPTION_FIELDS)
def test_a_template_body_cap_is_the_editorial_cap_where_there_is_room(field):
    if field in COMPUTED:
        assert pc.body_budget(field, "news:money_map", DATE, authorship=TEMPLATE) < \
            pc.body_budget(field, "news:money_map", DATE)   # the longer disclaimer costs room
    else:
        assert pc.body_budget(field, "news:money_map", DATE, authorship=TEMPLATE) == pc._BODY_CAPS[field]


@pytest.mark.parametrize("allow_x_url", [False, True])
@pytest.mark.parametrize("state", STATES)
def test_every_template_budget_plus_its_suffix_fits_and_never_hits_the_floor(allow_x_url, state):
    for category in NEWS_CATEGORIES + ("news:unknown_series",):
        for field in pc.CAPTION_FIELDS:
            suffix = pc._suffix(field, category, DATE, allow_x_url, state, authorship=TEMPLATE)
            budget = pc.body_budget(field, category, DATE, allow_x_url=allow_x_url, store_state=state,
                                    authorship=TEMPLATE)
            assert budget + pc.measured_length(field, suffix) <= pc.LIMITS[field], (field, category)
            if field in COMPUTED:
                assert pc.LIMITS[field] - pc.measured_length(field, suffix) > pc._MIN_BODY, (field, category)
            for bad in pc.FORBIDDEN_CHARS.get(field, ""):
                assert bad not in suffix, (field, repr(bad))


def test_the_tightest_template_budget_is_the_measured_one():
    """Preorder (the longest value line) with X URLs on, in the series with the longest tag
    (#industrytrends): 85 — still above _MIN_BODY. Every template SHORT headline is designed to fit it."""
    worst = min(pc.body_budget(f, c, DATE, allow_x_url=a, store_state=s, authorship=TEMPLATE)
                for f in COMPUTED for c in NEWS_CATEGORIES for a in (False, True) for s in STATES)
    assert worst == 85 == pc.body_budget("x", "news:theme_explainer", DATE, allow_x_url=True,
                                         store_state=pc.STORE_PREORDER, authorship=TEMPLATE)
    assert worst > pc._MIN_BODY


_NEWS_TAGS = {
    "news:ceo_buys": "#secfilings", "news:insider_buys": "#secfilings", "news:thirteen_f": "#secfilings",
    "news:congress_count": "#congress", "news:company_stakes": "#businessnews",
    "news:earnings": "#earnings", "news:money_map": "#businessmodel",
    "news:theme_explainer": "#industrytrends",
}


def test_the_news_hashtags_are_pinned():
    assert {k: v for k, v in pc._CATEGORY_TAG.items() if k.startswith("news:")} == _NEWS_TAGS
    assert set(_NEWS_TAGS) == set(NEWS_CATEGORIES)


@pytest.mark.parametrize("category", NEWS_CATEGORIES)
def test_a_news_category_gets_its_tag_and_the_base_tags_per_platform(category):
    tag = _NEWS_TAGS[category]
    assert pc.hashtags_for("x", category) == [tag]
    assert pc.hashtags_for("threads", category) == [tag]
    for field in ("tiktok", "instagram", "youtube_description", "linkedin"):
        assert pc.hashtags_for(field, category) == [tag, "#investing", "#financialliteracy"]
    for field in ("bluesky", "facebook", "youtube_title"):
        assert pc.hashtags_for(field, category) == []
    assert pc.hashtags_for("x", "news:unknown_series") == ["#investing"]


def test_every_shipped_series_has_its_news_tag():
    from app.services.marketing import selection

    series = getattr(selection, "SERIES", None)
    if series is None:
        pytest.skip("selection.SERIES (contract D3) is not declared yet")
    assert {f"news:{s.id}" for s in series} == set(NEWS_CATEGORIES)


# ── made_with_ai ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", ["video", "carousel", "image", "text", "podcast", "article"])
def test_an_ai_post_is_always_made_with_ai(fmt):
    assert pc.made_with_ai("ai", fmt) is True


@pytest.mark.parametrize("fmt, expected", [("video", True), ("image", False), ("text", False)])
def test_a_template_post_is_made_with_ai_only_as_a_narrated_video(fmt, expected):
    assert pc.made_with_ai(TEMPLATE, fmt) is expected


@pytest.mark.parametrize("fmt", ["carousel", "podcast", "article"])
def test_a_template_post_never_takes_a_format_the_templates_do_not_make(fmt):
    with pytest.raises(ValueError):
        pc.made_with_ai(TEMPLATE, fmt)


@pytest.mark.parametrize("fmt", ["", "Video", "video ", "reel", None, 1, ["video"], b"video"])
@pytest.mark.parametrize("authorship", ["ai", TEMPLATE])
def test_made_with_ai_refuses_an_unknown_format(fmt, authorship):
    with pytest.raises(ValueError):
        pc.made_with_ai(authorship, fmt)


def test_the_made_with_ai_format_tables_mirror_the_schema():
    from app.schemas import marketing as sch

    assert pc._POST_FORMATS == sch.POST_FORMATS
    assert pc._TEMPLATE_FORMATS == sch.FROZEN_POST_FORMATS


# ── an unknown authorship is a ValueError everywhere ─────────────────────────


@pytest.mark.parametrize("junk", JUNK_AUTHORSHIPS)
def test_every_function_refuses_an_unknown_authorship(junk):
    post = pc.compose("x", BODIES, category="news:ceo_buys", run_date=DATE)
    calls = [
        lambda: pc.disclaimer_long(DATE, authorship=junk),
        lambda: pc.disclaimer_long(DATE, video=True, authorship=junk),
        lambda: pc.disclaimer_short(junk),
        lambda: pc.disclaimer_card(DATE, junk),
        lambda: pc.disclaimer_for("x", DATE, junk),
        lambda: pc.disclaimer_for("youtube_title", DATE, junk),   # even where no disclaimer is worded
        lambda: pc._suffix("x", "news:ceo_buys", DATE, False, authorship=junk),
        lambda: pc.body_budget("x", "news:ceo_buys", DATE, authorship=junk),
        lambda: pc.body_budget("tiktok", "news:ceo_buys", DATE, authorship=junk),   # a fixed cap too
        lambda: pc.compose("x", BODIES, category="news:ceo_buys", run_date=DATE, authorship=junk),
        lambda: pc.compose("youtube", BODIES, category="news:ceo_buys", run_date=DATE, authorship=junk),
        lambda: pc.check_composed(post, DATE, junk),
        lambda: pc.image_footer(DATE, junk, source="SEC Form 4 filings", as_of="Filed Oct 5–9, 2026"),
        lambda: pc.made_with_ai(junk, "video"),
    ]
    for i, call in enumerate(calls):
        with pytest.raises(ValueError):
            call()


# ── the template image footer ────────────────────────────────────────────────

_TEMPLATE_FOOTER_GOLDEN = [
    ("SEC Form 4 filings", "Filed Oct 5–9, 2026",
     "Educational only · not investment advice · Source: SEC Form 4 filings · Filed Oct 5–9, 2026 · "
     "Caydex · Not affiliated with anyone named"),
    ("SEC Form 13F", "Quarter ended Sep 30, 2026 · filed Nov 14, 2026",
     "Educational only · not investment advice · Source: SEC Form 13F · Quarter ended Sep 30, 2026 · "
     "filed Nov 14, 2026 · Caydex · Not affiliated with anyone named"),
    ("company financial statements", "Fiscal 2025",
     "Educational only · not investment advice · Source: company financial statements · Fiscal 2025 · "
     "Caydex · Not affiliated with anyone named"),
    ("Nscale Form S-1 (Sep 18, 2026)", "As of Mar 27, 2026",
     "Educational only · not investment advice · Source: Nscale Form S-1 (Sep 18, 2026) · "
     "As of Mar 27, 2026 · Caydex · Not affiliated with anyone named"),
]


@pytest.mark.parametrize("source, as_of, golden", _TEMPLATE_FOOTER_GOLDEN)
def test_the_template_image_footer_is_pinned_verbatim(source, as_of, golden):
    from app.schemas.marketing import ONSCREEN_TEXT_MAX_CHARS, validate_onscreen_text

    for run_date in (DATE, date(2028, 2, 29)):   # the as-of label carries the date, not the run date
        assert pc.image_footer(run_date, TEMPLATE, source=source, as_of=as_of) == golden
        assert pc.image_footer(run_date, authorship=TEMPLATE, source=source, as_of=as_of) == golden
    assert golden.endswith(f"· {pc.PUBLISHER} · {pc.NON_AFFILIATION.rstrip('.')}")
    assert len(golden) <= ONSCREEN_TEXT_MAX_CHARS
    validate_onscreen_text([golden])   # declarable as on-screen text


@pytest.mark.parametrize("source, as_of, golden", _TEMPLATE_FOOTER_GOLDEN)
def test_the_template_image_footer_passes_the_public_copy_scan(source, as_of, golden):
    for kw in ({"allow_emoji": True}, {"allow_emoji": False, "strict_instruments": True},
               {"allow_emoji": False, "strict_instruments": False}):
        assert [(v.code, v.detail) for v in scan_text("f", clean(golden), **kw)] == _FOOTER_SCAN, kw
    assert clean(golden) == golden and "\n" not in golden
    assert not re.search(r"\bAI\b", golden) and "Written with AI" not in golden
    assert pc.x_link_tokens(golden) == []
    assert not contains_banned_copy(golden) and not contains_forecast(golden)
    low = golden.lower()
    for word in ("fmp", "financial modeling prep", "pick", "signal", "recommend", "buy", "sell", "price"):
        assert not re.search(rf"(?<![a-z]){re.escape(word)}", low), word


@pytest.mark.parametrize("kw", [
    {}, {"source": "SEC Form 4 filings"}, {"as_of": "Filed Oct 5–9, 2026"},
    {"source": None, "as_of": "Fiscal 2025"}, {"source": "SEC Form 13F", "as_of": None},
])
def test_the_template_image_footer_needs_its_source_and_as_of(kw):
    with pytest.raises(ValueError):
        pc.image_footer(DATE, TEMPLATE, **kw)


@pytest.mark.parametrize("bad", [
    "", "   ", " SEC Form 4 filings", "SEC Form 4 filings ", "SEC  Form 4", "SEC Form 4\nfilings",
    "SEC Form 4\tfilings", "SEC\x00Form 4", "SEC Form 4\u2028filings", "a" * (pc.FOOTER_SLOT_MAX_CHARS + 1),
    "FMP", "Data from fmp", "Financial Modeling Prep", "financialmodelingprep.com data",
    "https://www.sec.gov/edgar", "www.sec.gov", 4, b"SEC", ["SEC Form 4 filings"],
])
@pytest.mark.parametrize("slot", ["source", "as_of"])
def test_a_bad_template_footer_slot_is_refused(bad, slot):
    good = {"source": "SEC Form 4 filings", "as_of": "Filed Oct 5–9, 2026"}
    with pytest.raises(ValueError):
        pc.image_footer(DATE, TEMPLATE, **dict(good, **{slot: bad}))


def test_a_template_footer_slot_at_the_cap_is_accepted():
    from app.schemas.marketing import ONSCREEN_TEXT_MAX_CHARS

    footer = pc.image_footer(DATE, TEMPLATE, source="a" * pc.FOOTER_SLOT_MAX_CHARS,
                             as_of="b" * pc.FOOTER_SLOT_MAX_CHARS)
    assert len(footer) <= ONSCREEN_TEXT_MAX_CHARS   # two full slots still declarable on screen


@pytest.mark.parametrize("kw", [{"source": "SEC Form 4 filings"}, {"as_of": "Fiscal 2025"},
                                {"source": "SEC Form 13F", "as_of": "Fiscal 2025"}])
def test_the_ai_image_footer_refuses_a_source_or_as_of(kw):
    """The ai footer has no slot for them: a caller passing one has confused the two authorships."""
    with pytest.raises(ValueError):
        pc.image_footer(DATE, **kw)
    with pytest.raises(ValueError):
        pc.image_footer(DATE, "ai", **kw)
