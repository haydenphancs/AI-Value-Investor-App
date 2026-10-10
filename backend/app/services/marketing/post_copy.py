"""
Code-owned parts of every public post: hashtags, call to action, disclaimer (§12.5, rules §7).

The writer produces only the BODY of each caption. Everything that carries legal or commercial
weight is appended here, by code, from constants a test can pin — never trusted to a model:

* **Disclaimer** on every caption (long where there is room, short on X/Threads/Bluesky) and on
  the card burned into the end of every video. Publisher is `Caydex` — the developer is an Apple
  Individual account and `app/templates/legal/terms.html` says "operated by Caydex"; there is no
  "Caydex Inc.", so writing one would be a false statement of identity.
* **Call to action** per platform, opened by ONE code-owned VALUE LINE saying what Caydex is
  ("Caydex: AI research on public companies — on the App Store.", "… — pre-order on the App
  Store." during a pre-order, and the claim-free "Caydex: AI research on public companies." while
  the store URL is unset or invalid — by the store state threaded in from `smart_link`): TikTok/
  Instagram captions are not clickable ("Link in bio"); X is link-free unless `allow_x_url` (a URL
  makes an X post cost $0.20 instead of $0.015); everything else carries its own `/go/<platform>`
  smart link so arrivals are attributable. Nothing scans this copy at runtime, so tests pin it.
* **Hashtags** from a curated list (a model-written hashtag is how tickers and names get in).

Order is fixed: body → hashtags → CTA → disclaimer, and `check_composed` asserts the result ENDS
with the disclaimer, fits the platform and carries no character the platform refuses — nothing
is ever sliced to fit, and nothing is stripped at publish time.

**Authorship** (drop 2, owner decision 4 of 2026-10-09): every disclaimer function, the budget and
the composer take `authorship` — "ai" (the default: the class-A lesson writer; every Learn call
site is unchanged, byte for byte) or "template" (the code-owned news templates of classes C/F,
whose words come from a fixed template filled with public data). Template copy never says
"Written with AI assistance" / "AI-assisted": it says "Built by a fixed template from public
data." (+ "; narration voiced with AI." wherever a video is narrated) and always carries
NON_AFFILIATION. An unknown authorship raises ValueError — never a disclaimer that drops (or
invents) an AI line.

Pure; stdlib only (plus the Violation type).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app.services.marketing.compliance import Violation
# Every domain shape the link validator reads — a lower-case bare domain, the cased-gTLD form
# ("Learn.Money") and a cased glued abbreviation ("U.S.Markets"): X autolinks TLDs
# case-insensitively, and the counter must agree with the validator on what a link is.
from app.services.marketing.compliance import DOMAIN_SHAPE_RES, _is_abbreviation_run

PUBLISHER = "Caydex"
LINK_BASE_URL = "https://caydexinvest.com/go"

#: Outlets a Phase-2 package carries copy for (a subset of `schemas.marketing.POST_PLATFORMS`).
PLATFORMS: Tuple[str, ...] = (
    "tiktok", "youtube", "instagram", "facebook", "x", "threads", "bluesky", "linkedin",
)

#: Caption fields the writer fills (YouTube has two).
CAPTION_FIELDS: Tuple[str, ...] = (
    "tiktok", "youtube_title", "youtube_description", "instagram", "facebook", "x", "threads",
    "bluesky", "linkedin",
)

#: Hard platform limits on the COMPOSED text.
LIMITS: Dict[str, int] = {
    "tiktok": 2200, "youtube_title": 100, "youtube_description": 5000, "instagram": 2200,
    "facebook": 5000, "x": 280, "threads": 500, "bluesky": 300, "linkedin": 3000,
}

#: Editorial ceilings on the model-written BODY for platforms with room (short-form readers
#: stop early). X/Threads/Bluesky budgets are computed from the suffix instead.
_BODY_CAPS: Dict[str, int] = {
    "tiktok": 600, "youtube_title": 90, "youtube_description": 900, "instagram": 900,
    "facebook": 900, "linkedin": 1200,
}
_COMPUTED_BUDGET = frozenset({"x", "threads", "bluesky"})
#: Public name for the prompt side (`writer_prompts.caption_target`).
COMPUTED_BUDGET_FIELDS = _COMPUTED_BUDGET

#: Characters an outlet's API REFUSES in a field (a deterministic 400 no retry can fix), checked
#: on the composed text — which is built from `clean()`ed bodies, so NFKC has already turned a
#: full-width '＞' into '>'. The YouTube Data API accepts any UTF-8 in snippet.title /
#: snippet.description "except < and >" (invalidTitle / invalidDescription), and a title is one
#: line. '→' and '‹ ›' are allowed and are the natural replacements. Refused here — in
#: `check_composed`, so only that outlet is dropped — and never stripped at publish time: the
#: text published is exactly the text validated. (LinkedIn's reserved characters are escaped by
#: its adapter, not refused.)
_LINE_BREAKS = "\r\n\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"   # everything str.splitlines() splits on
FORBIDDEN_CHARS: Dict[str, str] = {
    "youtube_title": "<>" + _LINE_BREAKS,
    "youtube_description": "<>",
}
#: Room left for the model even after the suffix; below this the platform's copy is impossible.
_MIN_BODY = 80

_CATEGORY_TAG: Dict[str, str] = {
    # Money Moves categories
    "blueprints": "#businessstrategy", "battles": "#businesslessons",
    "valueTraps": "#investinglessons",
    # Journey levels
    "foundation": "#moneybasics", "analysis": "#investingbasics",
    "strategies": "#longterminvesting", "mastery": "#investorpsychology",
    # Company Weekly news series (drop 2): category "news:<series id>" (`news_templates.SeriesSpec`).
    # Never a person, ticker or cashtag; a series with no row here falls back to "#investing".
    "news:ceo_buys": "#secfilings", "news:insider_buys": "#secfilings",
    "news:thirteen_f": "#secfilings", "news:congress_count": "#congress",
    "news:company_stakes": "#businessnews", "news:earnings": "#earnings",
    "news:money_map": "#businessmodel", "news:theme_explainer": "#industrytrends",
}
_BASE_TAGS = ("#investing", "#financialliteracy")
_TAGS_PER_PLATFORM: Dict[str, int] = {
    "tiktok": 3, "instagram": 3, "youtube_description": 3, "linkedin": 3, "x": 1, "threads": 1,
}


def _date_label(run_date: date) -> str:
    return f"{run_date:%b} {run_date.day}, {run_date.year}"


#: Who wrote a post's words (drop 2, owner decision 4 of 2026-10-09). "ai": the class-A writer's
#: lesson — every disclaimer says AI wrote it. "template": the code-owned news templates (classes
#: C/F) — the words come from a fixed template filled with public data, so the copy says exactly
#: that, and only a narrated video discloses AI (its Kokoro voice). Mirrors
#: `schemas.marketing.TEMPLATE_AUTHORSHIP`; the default is "ai" everywhere.
AUTHORSHIP_AI = "ai"
AUTHORSHIP_TEMPLATE = "template"
AUTHORSHIPS: Tuple[str, ...] = (AUTHORSHIP_AI, AUTHORSHIP_TEMPLATE)
#: Template copy carries it ALWAYS (deterministic, and true for a company too): the disclaimer
#: of every caption, the video's disclaimer card and the image footer (without its full stop).
NON_AFFILIATION = "Not affiliated with anyone named."
#: What a template disclaimer says instead of an AI line (owner wording, 2026-10-09): the text
#: note on text/image captions, the video note wherever a narrated video is described.
TEMPLATE_NOTE_TEXT = "Built by a fixed template from public data."
TEMPLATE_NOTE_VIDEO = "Built by a fixed template from public data; narration voiced with AI."
#: The AI lines of "ai" copy. A template caption carrying one is an authorship mismatch
#: (`check_composed`): it would claim an AI wrote words a template wrote.
AI_DISCLOSURES: Tuple[str, ...] = (
    "Written with AI assistance", "AI-assisted", "Script and narration generated with AI",
)


def _authorship(value: Any, where: str) -> str:
    """`value` when it is one of AUTHORSHIPS, exactly; ValueError otherwise (a typo, wrong case,
    padding, None or a non-string) — never a disclaimer worded for a guess."""
    if isinstance(value, str) and value in AUTHORSHIPS:
        return value
    raise ValueError(f"{where}: unknown authorship {str(value)[:40]!r} (one of {AUTHORSHIPS})")


def disclaimer_long(run_date: date, *, video: bool = False, authorship: str = AUTHORSHIP_AI) -> str:
    if _authorship(authorship, "disclaimer_long") == AUTHORSHIP_TEMPLATE:
        note = f"{TEMPLATE_NOTE_VIDEO if video else TEMPLATE_NOTE_TEXT} {NON_AFFILIATION}"
    else:
        note = "Script and narration generated with AI." if video else "Written with AI assistance."
    return (
        f"{PUBLISHER} · Educational, impersonal information — not investment advice, "
        f"not a recommendation, not an offer. Investing involves risk, including loss of "
        f"principal. {note} {_date_label(run_date)}."
    )


def disclaimer_short(authorship: str = AUTHORSHIP_AI) -> str:
    if _authorship(authorship, "disclaimer_short") == AUTHORSHIP_TEMPLATE:
        return f"Educational only, not investment advice. {NON_AFFILIATION} {PUBLISHER}"
    return f"Educational only, not investment advice. AI-assisted. {PUBLISHER}"


def disclaimer_card(run_date: date, authorship: str = AUTHORSHIP_AI) -> str:
    """The text Phase 4 burns into the last video card (returned to the worker verbatim). A video
    is always narrated (Kokoro), so the template card carries the video note."""
    if _authorship(authorship, "disclaimer_card") == AUTHORSHIP_TEMPLATE:
        note = f"{TEMPLATE_NOTE_VIDEO} {NON_AFFILIATION}"
    else:
        note = "Script and narration generated with AI."
    return (
        "Educational, impersonal information — not investment advice. Investing involves "
        f"risk. {note} {PUBLISHER} · {_date_label(run_date)}"
    )


#: Who wrote an image's words: "ai" — the class-A writer's lesson (drop 1). "template" — the
#: code-owned news templates (drop 2), whose footer names its source and its as-of label instead of AI.
IMAGE_AUTHORSHIPS: Tuple[str, ...] = AUTHORSHIPS
#: A template footer's `source` / `as_of` slot: one line, no padding, no link, never the data
#: vendor's name (rules §1: never name or credit FMP on a public asset).
FOOTER_SLOT_MAX_CHARS = 160
_FOOTER_SLOT_BANNED_RE = re.compile(
    r"\bfmp\b|financial\s*modeling\s*prep|://|\bwww\.", re.IGNORECASE)
_SLOT_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _footer_slot(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"image_footer: the template footer needs a non-blank {name}")
    if value != value.strip() or "  " in value or _SLOT_CONTROL_RE.search(value):
        raise ValueError(f"image_footer: {name} must be one plain line with no padding")
    if len(value) > FOOTER_SLOT_MAX_CHARS:
        raise ValueError(f"image_footer: {name} is {len(value)} chars (max {FOOTER_SLOT_MAX_CHARS})")
    if _FOOTER_SLOT_BANNED_RE.search(value):
        raise ValueError(f"image_footer: {name} names the data vendor or carries a link")
    return value


def image_footer(run_date: date, authorship: str = AUTHORSHIP_AI, *, source: Optional[str] = None,
                 as_of: Optional[str] = None) -> str:
    """The footer the worker burns into every post image (drop 1, 2026-10-09) — code-owned copy the
    server then requires among the image's declared on-screen text. Pinned verbatim and run through
    the public-copy scan in tests/test_marketing_post_copy.py (nothing scans it at runtime).

    "ai" (a lesson image): the AI line and the run date; `source` / `as_of` are refused there.
    "template" (a news image, drop 2): no AI line — the image's words are a template's — but its
    `source` (a `company_news_rules.source_label`, never the data vendor) and its `as_of` label,
    both REQUIRED, and NON_AFFILIATION. ValueError for an unknown authorship or a bad slot: a
    template image must never carry an AI line it does not need, nor an AI image lose it."""
    if _authorship(authorship, "image_footer") == AUTHORSHIP_AI:
        if source is not None or as_of is not None:
            raise ValueError("image_footer: source / as_of belong to the 'template' footer only")
        return (f"Educational only · not investment advice · Written with AI assistance · "
                f"{_date_label(run_date)} · {PUBLISHER}")
    src = _footer_slot(source, "source")
    when = _footer_slot(as_of, "as_of")
    return (f"Educational only · not investment advice · Source: {src} · {when} · {PUBLISHER} · "
            f"{NON_AFFILIATION.rstrip('.')}")


#: Every `marketing_posts.format` (mirrors `schemas.marketing.POST_FORMATS`; a test pins them equal)
#: and the ones a template post can take (`schemas.marketing.FROZEN_POST_FORMATS`).
_POST_FORMATS: Tuple[str, ...] = ("video", "carousel", "image", "text", "podcast", "article")
_TEMPLATE_FORMATS: Tuple[str, ...] = ("video", "image", "text")


def made_with_ai(authorship: str, fmt: str) -> bool:
    """The platform "made with AI" flag a post of this authorship and format carries
    (`marketing_posts.metadata.made_with_ai`, stamped by create_posts; the X outlet ANDs it with
    MARKETING_X_MADE_WITH_AI). "ai" → always True (an AI wrote the words). "template" → True only
    for a video (its narration is voiced with AI); a template image or text post was written by a
    fixed template from public data (owner decision 4, 2026-10-09). ValueError for an unknown
    authorship or format, and for a format no template post takes — never a guessed flag."""
    who = _authorship(authorship, "made_with_ai")
    if not isinstance(fmt, str) or fmt not in _POST_FORMATS:
        raise ValueError(f"made_with_ai: unknown format {str(fmt)[:40]!r}")
    if who == AUTHORSHIP_AI:
        return True
    if fmt not in _TEMPLATE_FORMATS:
        raise ValueError(f"made_with_ai: a template post is never a {fmt!r} post")
    return fmt == "video"


def disclaimer_for(field: str, run_date: date, authorship: str = AUTHORSHIP_AI) -> Optional[str]:
    _authorship(authorship, "disclaimer_for")
    if field == "youtube_title":
        return None  # the description carries it; a 100-char title cannot
    if field in _COMPUTED_BUDGET:
        return disclaimer_short(authorship)
    return disclaimer_long(run_date, video=field in ("tiktok", "youtube_description", "instagram"),
                           authorship=authorship)


def hashtags_for(field: str, category: str) -> List[str]:
    n = _TAGS_PER_PLATFORM.get(field, 0)
    tags = [_CATEGORY_TAG.get(category, "#investing")] + list(_BASE_TAGS)
    out: List[str] = []
    for t in tags:
        if t not in out:
            out.append(t)
    return out[:n]


#: What the caption's code-owned VALUE LINE may say about the app: decided at WRITE time
#: (`smart_link.store_state()`, read once per generation by `script_service`) and threaded in here —
#: never read here, so this module stays pure. Prelaunch while MARKETING_APP_STORE_URL is unset or
#: invalid; preorder while it is valid and MARKETING_APP_STORE_PREORDER is on; live otherwise.
STORE_PRELAUNCH = "prelaunch"
STORE_PREORDER = "preorder"
STORE_LIVE = "live"
STORE_STATES: Tuple[str, ...] = (STORE_PRELAUNCH, STORE_PREORDER, STORE_LIVE)
#: The one sentence every caption but the YouTube title carries about what Caydex is (owner wording,
#: 2026-10-05). Nothing scans code-owned copy at runtime — compliance, grounding and the judge read
#: only model text — so tests/test_marketing_post_copy.py pins every line verbatim and runs it through
#: the public-copy scan. "on the App Store" stays true on a pre-order page too, so a forgotten flag
#: never makes a false claim; it never says "now", "available", "download" or "free". The PRELAUNCH
#: line makes no availability claim at all (2026-10-07): the app has been on the App Store since
#: 2026-10-05, so prelaunch is now reached only by an unset or mistyped URL — and "coming soon" would
#: then be false.
VALUE_PRODUCT = "Caydex: AI research on public companies"
_VALUE_LINES: Dict[str, str] = {
    STORE_PRELAUNCH: f"{VALUE_PRODUCT}.",
    STORE_PREORDER: f"{VALUE_PRODUCT} — pre-order on the App Store.",
    STORE_LIVE: f"{VALUE_PRODUCT} — on the App Store.",
}


def normalize_store_state(state: Any) -> str:
    """A member of STORE_STATES; anything else (None, a typo, wrong case, a non-string) is the
    PRELAUNCH state — the line that claims least."""
    return state if isinstance(state, str) and state in _VALUE_LINES else STORE_PRELAUNCH


def value_line(store_state: Any = STORE_PRELAUNCH) -> str:
    """The value line for a store state (an unknown state reads as prelaunch)."""
    return _VALUE_LINES[normalize_store_state(store_state)]


def cta_for(field: str, *, allow_x_url: bool = False, store_state: Any = STORE_PRELAUNCH) -> Optional[str]:
    """The code-owned call to action of a caption field: the value line, then where to go. TikTok and
    Instagram captions are not clickable ("Link in bio."); X is link-free unless `allow_x_url` (a URL
    makes an X post cost $0.20 instead of $0.015); everything else carries its own `/go/<platform>`
    smart link. The YouTube title carries none (the description does)."""
    if field == "youtube_title":
        return None
    line = value_line(store_state)
    if field in ("tiktok", "instagram"):
        return f"{line} Link in bio."
    if field == "x":
        return f"{line} {go_link('x')}" if allow_x_url else line
    platform = "youtube" if field == "youtube_description" else field
    return f"{line} Learn more: {go_link(platform)}"


def go_link(platform: str) -> str:
    """A platform's smart link — the ONE spelling `cta_for` writes and `carries_go_link` reads."""
    return f"{LINK_BASE_URL}/{platform}"


#: A character that would make `.../go/x` the start of a LONGER slug (`.../go/xyz`, `.../go/x_early`).
_SLUG_TAIL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def carries_go_link(platform: Any, caption: Any) -> bool:
    """Does `caption` carry `platform`'s OWN smart link — `go_link(platform)` not followed by another
    slug character, so `/go/x` is never `/go/xyz`? Pure; False for a non-string caption or an empty or
    non-string platform. Link-bearing today: bluesky, facebook, linkedin, threads and the YouTube
    description (campaign `youtube`); X only when its caption was composed with `allow_x_url`;
    TikTok and Instagram say "Link in bio.". The publisher opens a campaign's /go early window
    (`publish_clock`) only for such a post."""
    if not isinstance(platform, str) or not platform or not isinstance(caption, str):
        return False
    link = go_link(platform)
    start = 0
    while True:
        i = caption.find(link, start)
        if i < 0:
            return False
        end = i + len(link)
        if end >= len(caption) or caption[end] not in _SLUG_TAIL_CHARS:
            return True
        start = i + 1


def _suffix(field: str, category: str, run_date: date, allow_x_url: bool,
            store_state: Any = STORE_PRELAUNCH, *, authorship: str = AUTHORSHIP_AI) -> str:
    _authorship(authorship, "_suffix")
    parts: List[str] = []
    tags = hashtags_for(field, category)
    if tags:
        parts.append(" ".join(tags))
    cta = cta_for(field, allow_x_url=allow_x_url, store_state=store_state)
    if cta:
        parts.append(cta)
    disc = disclaimer_for(field, run_date, authorship)
    if disc:
        parts.append(disc)
    return "".join("\n\n" + p for p in parts)


# ── length accounting ─────────────────────────────────────────────────────────

_X_URL_WEIGHT = 23


def _x_char_weight(ch: str) -> int:
    o = ord(ch)
    if o <= 0x10FF or 0x2000 <= o <= 0x200D or 0x2010 <= o <= 0x201F or 0x2032 <= o <= 0x2037:
        return 1
    return 2


_WS_SPLIT_RE = re.compile(r"(\s+)")


def x_weighted_length(text: str) -> int:
    """twitter-text v3 weighting: most Latin = 1, CJK/emoji = 2, any URL = 23. A URL ends at ANY
    whitespace — splitting on spaces alone counted a URL glued to text by a newline character
    by character (over) or swallowed the text after it into its flat 23 (under). Whitespace is
    weighted like any other character."""
    total = 0
    for token in _WS_SPLIT_RE.split(text or ""):
        if token.startswith(("http://", "https://")):
            total += _X_URL_WEIGHT
        else:
            total += _weigh_with_bare_domains(token)
    return total


def _plain_typo(span: str) -> bool:
    """A glued abbreviation ("U.S.dollar", "e.g.the", "vs.the", "U.S.Economy") that X does not
    autolink: counted character by character, as X counts it. Round 3 (W3VAC-10, W3-SWW-3) kept
    its own list of tails known not to be TLDs here; round 4 (residual d) moved that list to
    `tlds.NON_TLD_TAILS` and made the link validator's exemption (`_is_abbreviation_run`) use
    it too, so the two agree: "U.S.markets", "U.S.Markets", "e.g.bank", "i.e.one", "e.g.you" and
    "vs.best" end in real gTLDs X DOES link — the validator rejects them and this counter weighs
    them as URLs. Over-counting costs a `too_long` the repair round can fix; under-counting would
    let X refuse an approved post at publish time."""
    return _is_abbreviation_run(span.lower())


def _domain_spans(token: str) -> List[Tuple[int, int]]:
    """The (start, end) spans of `token` X autolinks as a bare domain ("investor.gov"), without
    overlaps. A glued abbreviation whose tail is known not to be a TLD ("U.S.dollar", "e.g.the")
    is text, not a link (`_plain_typo`)."""
    spans = sorted(m.span() for rx in DOMAIN_SHAPE_RES for m in rx.finditer(token)
                   if not _plain_typo(m.group(0)))
    out: List[Tuple[int, int]] = []
    covered = [False] * len(token)
    for start, end in spans:
        if any(covered[start:end]):
            continue  # the other pattern already counted this domain
        covered[start:end] = [True] * (end - start)
        out.append((start, end))
    return out


def _weigh_with_bare_domains(token: str) -> int:
    """X autolinks a bare domain ("investor.gov") too and counts it as 23 whatever its length.
    The validators reject a domain in a body (`link`) EXCEPT a glued abbreviation whose tail is
    known not to be a TLD (`compliance._is_abbreviation_run`: "U.S.dollar", "e.g.the"); those
    are counted as text (`_plain_typo`), every other domain-shaped span as a URL."""
    spans = _domain_spans(token)
    if not spans:
        return sum(_x_char_weight(c) for c in token)
    covered = [False] * len(token)
    for start, end in spans:
        covered[start:end] = [True] * (end - start)
    rest = sum(_x_char_weight(c) for c, inside in zip(token, covered) if not inside)
    return rest + len(spans) * _X_URL_WEIGHT


def x_link_tokens(text: str) -> List[str]:
    """Every span X would turn into a link — a scheme URL token or a bare domain — in the order
    they appear. The SAME definition `x_weighted_length` counts at 23, so the publisher's
    publish-time URL guard (a post with a URL costs $0.20 instead of $0.015 on X) and the length
    counter can never disagree about what a link is."""
    out: List[str] = []
    for token in _WS_SPLIT_RE.split(text or ""):
        if not token or token.isspace():
            continue
        if token.startswith(("http://", "https://")):
            out.append(token)
            continue
        out.extend(token[start:end] for start, end in _domain_spans(token))
    return out


def measured_length(field: str, text: str) -> int:
    """Length as the platform counts it. Bluesky counts graphemes; code points are always ≥
    graphemes, so counting code points is the conservative choice with no extra dependency."""
    if field == "x":
        return x_weighted_length(text)
    return len(text or "")


def body_budget(field: str, category: str, run_date: date, *, allow_x_url: bool = False,
                store_state: Any = STORE_PRELAUNCH, authorship: str = AUTHORSHIP_AI) -> int:
    """Maximum body length for `field` (model-written, or a template's), after the code-owned
    suffix (which carries the run's value line and the authorship's disclaimer, so the budget
    depends on `store_state` and `authorship` too)."""
    _authorship(authorship, "body_budget")
    if field in _COMPUTED_BUDGET:
        # The suffix already starts with its own "\n\n" separator and its length is additive
        # (it is whitespace-separated from the body), so the budget is exactly what is left.
        suffix = _suffix(field, category, run_date, allow_x_url, store_state, authorship=authorship)
        return max(_MIN_BODY, LIMITS[field] - measured_length(field, suffix))
    return _BODY_CAPS[field]


# ── composition ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ComposedPost:
    platform: str
    title: Optional[str]
    caption: str

    def as_dict(self) -> Dict[str, Optional[str]]:
        return {"platform": self.platform, "title": self.title, "caption": self.caption}


def compose(platform: str, bodies: Dict[str, str], *, category: str, run_date: date,
            allow_x_url: bool = False, store_state: Any = STORE_PRELAUNCH,
            authorship: str = AUTHORSHIP_AI) -> ComposedPost:
    _authorship(authorship, "compose")
    if platform == "youtube":
        desc = bodies["youtube_description"] + _suffix(
            "youtube_description", category, run_date, allow_x_url, store_state, authorship=authorship)
        return ComposedPost("youtube", bodies["youtube_title"], desc)
    return ComposedPost(
        platform, None,
        bodies[platform] + _suffix(platform, category, run_date, allow_x_url, store_state,
                                   authorship=authorship),
    )


def _forbidden_chars(field: str, text: Optional[str]) -> List[Violation]:
    bad = FORBIDDEN_CHARS.get(field)
    if not bad or not text:
        return []
    found = [c for c in bad if c in text]   # ≤ 12 linear `in` scans
    if not found:
        return []
    brackets = [c for c in found if c not in _LINE_BREAKS]
    fixes = []
    if brackets:
        # Not "beats": the commonest '>' line in finance copy ("time in the market > timing
        # the market") becomes a famous saying with it (round 2, w2ww-3).
        fixes.append(f"{' and '.join(repr(c) for c in brackets)} is refused - rephrase it in "
                     "plain words, or use an arrow (→)")
    if len(brackets) < len(found):
        fixes.append("a line break is refused - keep the title on one line")
    return [Violation(field, "platform_forbidden_char", "; ".join(fixes))]


def check_composed(post: ComposedPost, run_date: date, authorship: str = AUTHORSHIP_AI) -> List[Violation]:
    """The composed caption ends with its disclaimer (the one `authorship` words) exactly once,
    fits the platform, and carries no character the platform's API refuses (`FORBIDDEN_CHARS`).
    A template post also must not carry an AI line ("authorship_mismatch"): it would claim an AI
    wrote what a template wrote. The "ai" checks are unchanged."""
    who = _authorship(authorship, "check_composed")
    out: List[Violation] = []
    field = "youtube_description" if post.platform == "youtube" else post.platform
    disc = disclaimer_for(field, run_date, who)
    if disc and (not post.caption.endswith(disc) or post.caption.count(disc) != 1):
        out.append(Violation(post.platform, "disclaimer_missing", "must end with the disclaimer once"))
    n = measured_length(field, post.caption)
    if n > LIMITS[field]:
        out.append(Violation(post.platform, "over_platform_limit", f"{n} > {LIMITS[field]}"))
    out.extend(_forbidden_chars(field, post.caption))
    if post.platform == "youtube":
        t = measured_length("youtube_title", post.title or "")
        if not post.title or t > LIMITS["youtube_title"]:
            out.append(Violation("youtube", "over_platform_limit", f"title {t}"))
        out.extend(_forbidden_chars("youtube_title", post.title))
    if who == AUTHORSHIP_TEMPLATE:
        text = f"{post.title or ''}\n{post.caption}".casefold()
        said = [line for line in AI_DISCLOSURES if line.casefold() in text]
        if said:
            out.append(Violation(post.platform, "authorship_mismatch",
                                 f"a template post says {said[0]!r} - its words are a template's"))
    return out
