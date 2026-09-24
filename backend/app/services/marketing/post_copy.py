"""
Code-owned parts of every public post: hashtags, call to action, disclaimer (§12.5, rules §7).

The writer produces only the BODY of each caption. Everything that carries legal or commercial
weight is appended here, by code, from constants a test can pin — never trusted to a model:

* **Disclaimer** on every caption (long where there is room, short on X/Threads/Bluesky) and on
  the card burned into the end of every video. Publisher is `Caydex` — the developer is an Apple
  Individual account and `app/templates/legal/terms.html` says "operated by Caydex"; there is no
  "Caydex Inc.", so writing one would be a false statement of identity.
* **Call to action** per platform: TikTok/Instagram captions are not clickable ("Link in bio");
  X is link-free unless `allow_x_url` (a URL makes an X post cost $0.20 instead of $0.015);
  everything else carries its own `/go/<platform>` smart link so arrivals are attributable.
* **Hashtags** from a curated list (a model-written hashtag is how tickers and names get in).

Order is fixed: body → hashtags → CTA → disclaimer, and `check_composed` asserts the result ENDS
with the disclaimer, fits the platform and carries no character the platform refuses — nothing
is ever sliced to fit, and nothing is stripped at publish time.

Pure; stdlib only (plus the Violation type).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

from app.services.marketing.compliance import BARE_DOMAIN_RE, Violation
# The cased-gTLD form ("Learn.Money") — X autolinks TLDs case-insensitively.
from app.services.marketing.compliance import _CASED_GTLD_RE, _is_abbreviation_run

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
}
_BASE_TAGS = ("#investing", "#financialliteracy")
_TAGS_PER_PLATFORM: Dict[str, int] = {
    "tiktok": 3, "instagram": 3, "youtube_description": 3, "linkedin": 3, "x": 1, "threads": 1,
}


def _date_label(run_date: date) -> str:
    return f"{run_date:%b} {run_date.day}, {run_date.year}"


def disclaimer_long(run_date: date, *, video: bool = False) -> str:
    ai = "Script and narration generated with AI." if video else "Written with AI assistance."
    return (
        f"{PUBLISHER} · Educational, impersonal information — not investment advice, "
        f"not a recommendation, not an offer. Investing involves risk, including loss of "
        f"principal. {ai} {_date_label(run_date)}."
    )


def disclaimer_short() -> str:
    return f"Educational only, not investment advice. AI-assisted. {PUBLISHER}"


def disclaimer_card(run_date: date) -> str:
    """The text Phase 4 burns into the last video card (returned to the worker verbatim)."""
    return (
        "Educational, impersonal information — not investment advice. Investing involves "
        f"risk. Script and narration generated with AI. {PUBLISHER} · {_date_label(run_date)}"
    )


def disclaimer_for(field: str, run_date: date) -> Optional[str]:
    if field == "youtube_title":
        return None  # the description carries it; a 100-char title cannot
    if field in _COMPUTED_BUDGET:
        return disclaimer_short()
    return disclaimer_long(run_date, video=field in ("tiktok", "youtube_description", "instagram"))


def hashtags_for(field: str, category: str) -> List[str]:
    n = _TAGS_PER_PLATFORM.get(field, 0)
    tags = [_CATEGORY_TAG.get(category, "#investing")] + list(_BASE_TAGS)
    out: List[str] = []
    for t in tags:
        if t not in out:
            out.append(t)
    return out[:n]


def cta_for(field: str, *, allow_x_url: bool = False) -> Optional[str]:
    if field in ("tiktok", "instagram"):
        return "Link in bio."
    if field == "x":
        return f"{LINK_BASE_URL}/x" if allow_x_url else None
    if field == "youtube_title":
        return None
    platform = "youtube" if field == "youtube_description" else field
    return f"Learn more: {LINK_BASE_URL}/{platform}"


def _suffix(field: str, category: str, run_date: date, allow_x_url: bool) -> str:
    parts: List[str] = []
    tags = hashtags_for(field, category)
    if tags:
        parts.append(" ".join(tags))
    cta = cta_for(field, allow_x_url=allow_x_url)
    if cta:
        parts.append(cta)
    disc = disclaimer_for(field, run_date)
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


#: The last label of a glued-abbreviation typo that X can NOT autolink: common English words
#: checked against the IANA root zone (tlds-alpha-by-domain, version 2026072500) — none is a TLD.
#: Round 3 (W3VAC-10, W3-SWW-3). Deliberately narrower than the link validator's exemption
#: (`compliance._is_abbreviation_run`, which trusts its own short TLD list): "U.S.markets",
#: "e.g.bank", "i.e.one", "e.g.you" and "vs.best" end in real gTLDs that X DOES link, so they keep
#: the URL weight. Over-counting costs a `too_long` the repair round can fix; under-counting would
#: let X refuse an approved post at publish time.
_NON_TLD_TAILS = frozenset("""
the and but for nor yet not its our their this that these those with from into onto than then
when what why who whom whose which while where was were are has had have will can may all any
some each such same own other also just only even still very well yes via per etc of or on an
he she his her him we they them let dollar dollars economy economies stock stocks shares share
price prices cost costs sales fee fees rate rates bonds oil index dow firms firm companies banks
consumers consumer government federal treasury treasuries inflation interest debt job wages wage
housing home rents taxes growth gdp exports imports trades retailers retail
""".split())


def _plain_typo(span: str) -> bool:
    """A glued abbreviation ("U.S.dollar", "e.g.the", "vs.the") that the link validator lets
    through AND X does not autolink: counted character by character, as X counts it."""
    low = span.lower()
    return _is_abbreviation_run(low) and low.rsplit(".", 1)[-1] in _NON_TLD_TAILS


def _weigh_with_bare_domains(token: str) -> int:
    """X autolinks a bare domain ("investor.gov") too and counts it as 23 whatever its length.
    The validators reject a domain in a body (`link`) EXCEPT a glued abbreviation
    (`compliance._is_abbreviation_run`: "U.S.dollar", "e.g.the"); the ones X cannot link either
    are counted as text (`_plain_typo`), every other domain-shaped span as a URL."""
    spans = [m.span() for rx in (BARE_DOMAIN_RE, _CASED_GTLD_RE) for m in rx.finditer(token)
             if not _plain_typo(m.group(0))]
    if not spans:
        return sum(_x_char_weight(c) for c in token)
    covered = [False] * len(token)
    domains = 0
    for start, end in sorted(spans):
        if any(covered[start:end]):
            continue  # the other pattern already counted this domain
        covered[start:end] = [True] * (end - start)
        domains += 1
    rest = sum(_x_char_weight(c) for c, inside in zip(token, covered) if not inside)
    return rest + domains * _X_URL_WEIGHT


def measured_length(field: str, text: str) -> int:
    """Length as the platform counts it. Bluesky counts graphemes; code points are always ≥
    graphemes, so counting code points is the conservative choice with no extra dependency."""
    if field == "x":
        return x_weighted_length(text)
    return len(text or "")


def body_budget(field: str, category: str, run_date: date, *, allow_x_url: bool = False) -> int:
    """Maximum model-written body length for `field`, after the code-owned suffix."""
    if field in _COMPUTED_BUDGET:
        # The suffix already starts with its own "\n\n" separator and its length is additive
        # (it is whitespace-separated from the body), so the budget is exactly what is left.
        suffix = _suffix(field, category, run_date, allow_x_url)
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
            allow_x_url: bool = False) -> ComposedPost:
    if platform == "youtube":
        desc = bodies["youtube_description"] + _suffix(
            "youtube_description", category, run_date, allow_x_url)
        return ComposedPost("youtube", bodies["youtube_title"], desc)
    return ComposedPost(
        platform, None, bodies[platform] + _suffix(platform, category, run_date, allow_x_url),
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


def check_composed(post: ComposedPost, run_date: date) -> List[Violation]:
    """The composed caption ends with its disclaimer exactly once, fits the platform, and
    carries no character the platform's API refuses (`FORBIDDEN_CHARS`)."""
    out: List[Violation] = []
    field = "youtube_description" if post.platform == "youtube" else post.platform
    disc = disclaimer_for(field, run_date)
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
    return out
