"""
PER-POST ART for a single-company template post — owner decisions 2026-11-15: "Google draws, Claude checks",
"Picture and video cover", main lane "Video cover now, pictures later"; and "we don't change anything at the
money moves".

One accepted template output (the record already chosen, composed and re-checked) → at most ONE new 16:9
picture in the Money Moves art style, stored public and content-addressed, or a record saying why there is none:

    name-free company facts (display name, sector / industry, the top revenue line — never a figure, a person,
    a ticker, the filing, the trade or the headline)
      └─ (a) BRIEF  — Claude writes one subject sentence + a verdict-neutral treatment (MARKETING_TREATMENTS);
                      one more brief only when the first answer's subject is unusable (`subject_problem`)
      └─ (b) DRAW   — the image model (`GeminiClient.generate_image`, MARKETING_ART_IMAGE_MODEL, 16:9, 2K) from
                      `image_prompt`: the Money Moves prompt (`money_moves_art_style.prompt_for`, palette ALWAYS
                      "blueprints") + MARKETING_ADDENDUM
      └─ (c) NORMALISE — off the event loop: header-first decode, pixel-bomb guard, `style.fit_to` to
                      ART_WIDTH × ART_HEIGHT, `style.save_master` (q95, re-encoded at q85 over ART_MAX_BYTES),
                      decoded back in full
      └─ (d) CHECK  — Claude inspects THOSE EXACT JPEG bytes (vision): text or numbers, a logo or brand, people,
                      a chart / money / flag, alarming or judgemental imagery, the object recognisable
      └─ (e) one REDRAW on a content fail (code-owned words only: `redraw_prompt`), checked again — a second
                      fail is `check_failed` and nothing is stored
      └─ (f) STORE  — only a picture that PASSED: `store(jpeg, width=, height=)` (run_service.store_art: public
                      `marketing-media/art/<sha256[:32]>.jpg`, never overwritten or deleted, no asset row)

`make_post_art` NEVER raises (cancellation aside): every failure — a missing key, an open breaker, a refusal, a
failed check, a timeout, a bug — is a record with `status: "failed"` and one of ART_REASONS, and the day goes on
with today's drawing. Art never refuses, skips or fails a day. Bounded by MARKETING_ART_BUDGET_SECONDS (read at
call time): a step starts only while its minimum and the minimum of every step after it still fit (STEP_SECONDS),
and each Claude call gets a client deadline inside its slice, so it ends in the client's typed timeout rather than
being cancelled from outside.

Spend (decision 10): the record's `usage` counts the images that answered, the image calls whose billing is
unknown (`images_uncertain`: an image call that returned no picture — a refusal, an outage, a timeout — at
IMAGE_MAX_ATTEMPTS each, its worst case), every Claude token by model and kind, and the Claude calls whose billing
is unknown (`claude_uncertain`: a client timeout, a dropped connection, our own cut-off — each priced at
`claude_call_worst_micros`). `estimated_micros` prices it all; the monthly cap reads it back. One residual: a
generate_image call that SUCCEEDED after an internal transient retry reports one image — the retry's possible bill
is invisible here (the integration logs it), and `run_worst_micros` reserves for it.

No Gemini fallback for the brief or the check (a Gemini check would be Google grading Google's picture, and art
is optional): Claude is reached ONLY through `llm_provider.ClaudeOnly(..., breaker=BREAKER_ACCOUNT)` — the shared
breaker opens only on used-up credits or an auth refusal, never on a 429, an overload or a timeout. The art calls
are not a provider ROLE, so the script lease is unchanged.

Money Moves is imported READ-ONLY (`app.services.money_moves_art_style`: `prompt_for`, `TREATMENTS`, `PALETTES`,
`ARTICLES`, `default_treatment`, `subject_problem`, `fit_to`, `save_master`); this module defines none of that
system's names (pinned by tests/test_marketing_post_art.py, read from tests/test_money_moves_art_style.py's AST)
and never imports `learn_weekly/cover_art.py`.

Identity rule (CLAUDE.md #7): models are named only in the server-side record (`output.art.models`, `usage`) and
logs; the worker's read-back gets `worker_art` (url, sha256, bytes, width, height) and the public label says
"AI illustration" (`template_onscreen.ART_LABEL`). No prompt here names a model or a provider.

Imports: stdlib, settings, the style module, `template_onscreen` (pure); the gemini / Claude clients,
`llm_provider` and `digest_service` (prices) only inside functions. FMP-free: the company's sector / industry
arrive through the caller's `facts` / `facts_fn` (`company_news_adapter.company_facts`, reached by the script flow).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.config import settings
from app.services import money_moves_art_style as style
from app.services.marketing.template_onscreen import SHA256_RE

logger = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────────────────────────

#: Recorded in `output.art.version`. Bump it with ANY change to a prompt, the addendum, the check, the treatment
#: list or the normalisation (and re-pin the prompt hashes in tests/test_marketing_post_art.py).
ART_VERSION = "art-v1"
#: The versions a stored record may carry and still be drawn (`drawable_art`).
ART_VERSIONS: Tuple[str, ...] = (ART_VERSION,)

#: Storage: `marketing-media/art/<sha256[:32]>.jpg` (marketing.md §4, the logos precedent — content-addressed, no
#: asset row, never overwritten or deleted). The worker's `marketing/art.py` pins these equal.
ART_PREFIX = "art"
ART_EXT = "jpg"
ART_CONTENT_TYPE = "image/jpeg"
#: The stored picture's byte cap (the Money Moves objects' too; a 1376×774 q95 master is ~0.3-0.9 MB).
ART_MAX_BYTES = 1_900_000
#: The stored picture's size: native 16:9, ~1.06 MP — under Claude's high-resolution vision bound (2,576 px on the
#: long edge), so the check sees exactly the pixels that are published, and above every band the worker draws it
#: into (the 952-px `feature` band, the ~920-px opening-card band).
ART_WIDTH = 1376
ART_HEIGHT = 774
#: What `drawable_art` / the worker accept (pinned equal in the worker): a 16:9 picture 1024..2752 px wide.
ART_MIN_WIDTH = 1024
ART_MAX_WIDTH = 2752
ART_ASPECT = 16 / 9
ART_ASPECT_TOLERANCE = 0.01          # |w/h − 16/9|

#: The image call. 2K costs what 1K does and is downscaled to ART_WIDTH (supersampled detail).
IMAGE_ASPECT_RATIO = "16:9"
IMAGE_SIZE = "2K"
#: `generate_image`'s own attempts per draw (transport / timeout / 5xx / 429 / empty answers retried inside).
IMAGE_MAX_ATTEMPTS = 2
IMAGE_BACKOFF_SECONDS = 4.0
#: Draws at most per run: the first and the one redraw.
ART_DRAWS = 2
#: Claude calls at most per run: two briefs (one retry for an unusable subject) and two checks (the redraw's).
ART_CLAUDE_CALLS = 4
#: The model's raw picture (before normalisation): PNG or JPEG, one frame, at least this wide, at most this many
#: pixels (a 2K 16:9 answer is ~2752×1536; anything near a pixel bomb is refused before it is decoded), and within
#: this RELATIVE tolerance of 16:9 (fit_to then trims a pixel or two; a square answer is refused, never cropped).
ART_SOURCE_FORMATS: Tuple[str, ...] = ("PNG", "JPEG")
ART_MIN_SOURCE_WIDTH = 1024
ART_MAX_SOURCE_PIXELS = 4096 * 2304
ART_SOURCE_ASPECT_TOLERANCE = 0.03
#: The re-encode quality when the q95 master is over ART_MAX_BYTES.
ART_FALLBACK_QUALITY = 85

#: The palette, ALWAYS (owner decision 2026-11-15): Money Moves' "blueprints" — cold steel-blue on slate, which
#: sits on Caydex's navy cards. The other palettes read as verdicts on a named company (valueTraps' oxblood =
#: failure) or need two rival objects (battles).
PALETTE = "blueprints"
#: The verdict-neutral treatments a brief may pick (⊆ style.TREATMENTS, pinned). Left out: specimen_plate (a
#: post-mortem), ledger_still ("quietly damning"), exploded_kit (a thing taken apart), macro_noir (low-key, the
#: empty room) and found_artefact (the evidence print).
MARKETING_TREATMENTS: Tuple[str, ...] = (
    "duotone_press", "cutout_grid", "colour_field", "flat_deco", "blueprint_cyanotype", "tech_noir",
)

#: Usage tags (the CLAUDE_USAGE / GEMINI_USAGE log lines). None is an llm_provider ROLE (pinned).
BRIEF_USAGE_TAG = "marketing_art_brief"
CHECK_USAGE_TAG = "marketing_art_check"
IMAGE_USAGE_TAG = "marketing_post_art"
#: Opus 5.5 always thinks: every art call runs at effort low with max_tokens = CAYDEX_CLAUDE_MAX_TOKENS.
ART_EFFORT = "low"
#: The input tokens one art call is priced at in the worst case (the check: ~1.4k image tokens for ART_WIDTH ×
#: ART_HEIGHT + ~1k of prompt; the brief is smaller). A call whose billing is unknown is priced at this input plus
#: CAYDEX_CLAUDE_MAX_TOKENS output.
CLAUDE_WORST_INPUT_TOKENS = 6_000

#: Each step's (minimum to start, cap) in seconds. A step starts only while its minimum plus the minimum of every
#: step that must follow still fits in what is left of the art budget; it then gets min(cap, left − what follows).
#: The happy path's minimums fit the budget setting's floor (60 s) and every Claude cap fits the client's own
#: default deadline (pinned). "facts" is optional: too little time → no facts, never a failure.
STEP_SECONDS: Dict[str, Tuple[float, float]] = {
    "facts": (1.0, 8.0),
    "brief": (10.0, 45.0),
    "image": (25.0, 100.0),
    "normalise": (5.0, 20.0),
    "check": (10.0, 45.0),
    "store": (5.0, 15.0),
}
#: The steps of the happy path, in order; the redraw repeats its last four.
HAPPY_PATH: Tuple[str, ...] = ("brief", "image", "normalise", "check", "store")
REDRAW_PATH: Tuple[str, ...] = HAPPY_PATH[1:]
#: The Claude client's own deadline ends this much before its slice does, so its typed timeout wins the race.
CLAUDE_CLIENT_MARGIN_SECONDS = 2.0

#: A subject sentence: the style's floor (40 characters) up to this cap — checked BEFORE any regex.
SUBJECT_MAX_CHARS = 600
#: The check's free-text notes (Telegram only, the owner): folded onto one line and capped.
NOTES_MAX_CHARS = 200
#: A fact (sector, industry, top revenue line) the brief may read: a short plain name, no digit (never a figure).
FACT_MAX_CHARS = 60
FACT_KEYS: Tuple[str, ...] = ("sector", "industry", "top_line")

#: Where the art may be drawn (decision 4).
SURFACE_PICTURE = "picture"     # the spotlight `feature` picture's band
SURFACE_COVER = "cover"         # the template video's opening card (the TikTok / Instagram cover)
SURFACES: Tuple[str, ...] = (SURFACE_PICTURE, SURFACE_COVER)
#: The series that may carry art on the `feature` PICTURE and the cover. Owner decisions 2026-11-20 ("Drop it from
#: insider posts (Recommended)"): NONE — the insider spotlight, its only member since 2026-11-15, is now a LIST of 3-5
#: companies (one illustration cannot stand for them, and the list needs the picture's height); the 13F spotlight is
#: always a list of two to six holdings (news-v23). Kept (empty) so a stored `feature` output still reads.
PICTURE_SERIES: Tuple[str, ...] = ()
#: Main-lane series that may carry art on the video COVER only ("Video cover now, pictures later"), and only when
#: the record names exactly ONE company. NONE today — owner decision 2026-11-20, asked "Drop it from insider posts
#: (Recommended) :: Keep the code but use it nowhere for now; the switch stays off" against "Put it on Money Map
#: covers", he chose the first: the art code is kept and reachable from NO series, so even with MARKETING_ART_ENABLED
#: on, `art_subject_for` answers None for every post and nothing is spent. The Money Map, the last one-company series,
#: is in NEVER_ART_SERIES until the owner asks for art again (then: move it back here, and the tests that exercise the
#: kept pipeline — which monkeypatch it back — become the real path). news-v23 (the restyle, owner 2026-11-19): a
#: Congress Count (2-3 companies) and an earnings post (2-4 companies) are ALWAYS several companies, their cover a list
#: — never art.
COVER_ONLY_SERIES: Tuple[str, ...] = ()
ART_SERIES: frozenset = frozenset(PICTURE_SERIES + COVER_ONLY_SERIES)
#: Never art (decision 4): the 13F roundup, the stake pair, the theme grid — and every lesson; since news-v23 the 13F
#: spotlight, the Congress Count and earnings too (a list of several companies: one illustration cannot stand for them);
#: since the owner decisions of 2026-11-20 EVERY insider series ("Drop it from insider posts (Recommended)") and the
#: Money Map ("use it nowhere for now") — every shipped series.
NEVER_ART_SERIES: frozenset = frozenset({"thirteen_f", "thirteen_f_spotlight", "company_stakes", "theme_explainer",
                                         "congress_count", "earnings", "ceo_buys", "insider_buys",
                                         "insider_spotlight", "money_map"})

#: Record statuses.
STATUS_OK = "ok"
STATUS_FAILED = "failed"

#: Why there is no art — a CLOSED set (the review line words each one: REASON_WORDS). The first five,
#: `ledger_unreadable` (the run or its script row could not be read before any spend) and `costs_cover` (owner decision
#: 2026-11-20: a Money Map LOSS year's video cover is its costs card — `cover_is_costs_card` — so a picture nobody would
#: draw is never paid for) are the caller's gates (`script_service._attach_art`), the rest this module's.
ART_REASONS: Tuple[str, ...] = (
    "not_a_company_series", "no_capability", "text_only_day", "budget_cap", "budget_unreadable",
    "claude_unavailable", "claude_unpriced", "image_model_invalid", "brief_unusable", "image_refused",
    "image_unavailable", "image_invalid", "check_failed", "check_unreadable", "storage", "timeout",
    "internal_error", "ledger_unreadable", "costs_cover",
)
#: The owner's words for each reason ("Art: none today — <words>"). No model or provider is named.
REASON_WORDS: Dict[str, str] = {
    "not_a_company_series": "this post is not about a single company",
    "no_capability": "the media worker cannot draw art yet",
    "text_only_day": "today's posts carry no picture or video",
    "budget_cap": "this month's art budget is used up",
    "budget_unreadable": "this month's art spend could not be read",
    "claude_unavailable": "the art checker could not be reached",
    "claude_unpriced": "the art checker's model has no price set",
    "image_model_invalid": "the drawing model setting is not usable",
    "brief_unusable": "no usable picture idea came back",
    "image_refused": "the drawing was refused",
    "image_unavailable": "the drawing service could not be reached",
    "image_invalid": "the drawing came back unusable",
    "check_failed": "the picture check failed",
    "check_unreadable": "the picture check gave no usable answer",
    "storage": "the picture could not be stored",
    "timeout": "the art step ran out of time",
    "internal_error": "an internal error (see the logs)",
    "ledger_unreadable": "the run's records could not be read",
    "costs_cover": "a loss year's video cover is its costs chart",
}


def cover_is_costs_card(output: Any) -> bool:
    """Whether a template output's video cover is a Money Map LOSS year's COSTS card (owner decision 2026-11-20,
    money_map_loss_A_rivian_cover.jpg: its opening card carries `costs`) — a cover that draws no art, so none is made
    for it (`script_service._attach_art`: `costs_cover`). Pure, never raises."""
    opening = output.get("opening_card") if isinstance(output, Mapping) else None
    return isinstance(opening, Mapping) and opening.get("costs") is not None

#: The check's five content flags, in this order, plus `shows_the_subject` (True = the object is recognisable).
CHECK_FLAGS: Tuple[str, ...] = (
    "text_or_numbers", "logo_or_brand", "people_faces_or_hands", "chart_money_or_flag", "alarming_or_judgemental",
)
NOT_RECOGNISABLE = "not_recognisable"
#: flag → (the redraw prompt's fixed words, the owner's words). ONLY these go into a redraw prompt — never the
#: checker's own notes (model text never steers the drawing).
FLAG_WORDS: Dict[str, Tuple[str, str]] = {
    "text_or_numbers": ("text, letters or numbers", "text or numbers"),
    "logo_or_brand": ("a logo or a brand mark", "a logo or brand"),
    "people_faces_or_hands": ("people, faces or hands", "people, faces or hands"),
    "chart_money_or_flag": ("a chart, an arrow, money or a flag", "a chart, money or a flag"),
    "alarming_or_judgemental": ("alarming or judgemental imagery", "alarming or judgemental imagery"),
    NOT_RECOGNISABLE: ("an object that could not be recognised", "the object could not be recognised"),
}

#: The marketing deny-list a subject sentence may not contain (on top of the style's brand / person list, the
#: company's own names and tickers): charts and data, money, politics, verdict and alarm imagery. One alternation
#: of literal words under word boundaries — linear (pinned by the sweep). It matches a negation too ("with no
#: charts"): the brief is told never to mention them at all.
_ART_SUBJECT_BANNED_RE = re.compile(
    r"\b(?:"
    r"charts?|graphs?|diagrams?|arrows?|candlesticks?|tickers?|dashboards?|stock markets?|trading floors?|"
    r"money|cash|banknotes?|bank ?notes?|coins?|currency|currencies|dollars?|euros?|bitcoins?|bullion|"
    r"gold bars?|price tags?|piggy banks?|"
    r"flags?|capitol|congress|senate|government|parliament|ballots?|elections?|"
    r"trophy|trophies|crowns?|medals?|podiums?|"
    r"rockets?|soaring|rising|falling|upward|downward|collapsed?|collapsing|crash(?:ed|es|ing)?|"
    r"cracked|broken|shattered|ruins?|fires?|flames?|burning|smoke|smoking|storms?|lightning|"
    r"skulls?|tombstones?|graves?|bombs?|explosions?|exploding|bulls?|bears?"
    r")\b",
    re.IGNORECASE,
)
#: One word of a name ("amazon", "o'reilly"), for the company-name deny-list.
_WORD_RE = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")
#: A canonical common-stock symbol (company_news_rules' dash form).
_SYMBOL_RE = re.compile(r"[A-Z]{1,5}(?:-[A-C])?")
#: A fact the brief may read: letters and plain punctuation only — no digit, no URL, no line break.
_FACT_RE = re.compile(r"[A-Za-z][A-Za-z &,'’()/.\-]*")
#: A model id: short and plain (anything else is shown by length only).
_MODEL_RE = re.compile(r"[a-z0-9][a-z0-9.\-]{0,63}")
#: A model that moves or vanishes (§7a): a "preview" or "latest" token anywhere in the id.
_MOVING_MODEL_RE = re.compile(r"(?:^|-)(?:preview|latest)(?:-|$)")
#: Corporate suffixes dropped from a company name before it joins the deny-list (whole names only).
_NAME_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "com", "company", "companies", "ltd", "limited", "plc",
    "llc", "lp", "sa", "nv", "ag", "se", "holdings", "holding", "group", "the", "class", "a", "b", "c", "de",
    "adr", "ads",
})
#: A company-name phrase this long or longer joins the deny-list lower-case, as a whole phrase; a SHORTER one ("HP",
#: "3M") joins like a ticker — upper-case, case-SENSITIVE — when it is at least MIN_TICKER_BAN_CHARS long and holds a
#: letter (C-fix, 2026-11-16: dropping it let "an HP laptop" through for HP Inc., whose ticker is HPQ). A ticker
#: shorter than MIN_TICKER_BAN_CHARS never joins (a one-letter ticker, case-sensitive, would match the article "A"
#: that opens most subjects); neither does a one-letter or digit-only name.
MIN_NAME_BAN_CHARS = 3
MIN_TICKER_BAN_CHARS = 2
#: `subject_problem`'s answer for a subject naming the company (its matched term follows in brackets, for the log —
#: the brief's retry gets these words alone, never the term).
_COMPANY_NAMED = "names the company or its ticker"

_logged_once: set = set()


def _error_once(key: str, msg: str, *args: Any) -> None:
    if key not in _logged_once:
        _logged_once.add(key)
        logger.error(msg, *args)


def _shown(raw: Any) -> str:
    """A setting value as it may be logged: a plain model id as is, anything else by length."""
    s = "" if raw is None else str(raw)
    return repr(s) if _MODEL_RE.fullmatch(s) else f"<{len(s)} chars, not shown>"


def _fold(text: Any, cap: int) -> str:
    """One line, whitespace collapsed, printable only, capped."""
    if not isinstance(text, str):
        return ""
    s = " ".join(text[: cap * 4].split())
    return "".join(ch for ch in s if ch.isprintable())[:cap]


# ── the subject company (decision 4) ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArtSubject:
    """The ONE company a post's art pictures, where it may be drawn, and the words its subject may not hold."""

    symbol: str
    name: str
    surfaces: Tuple[str, ...]
    top_line: Optional[str] = None
    banned: Tuple[str, ...] = field(default=())


def _logo_names(output: Mapping[str, Any]) -> Dict[str, str]:
    """{symbol: display name} from `output.logos` (attached by the build), else `output.logo_refs` (the compose)."""
    out: Dict[str, str] = {}
    for key in ("logos", "logo_refs"):
        entries = output.get(key)
        for entry in entries if isinstance(entries, list) else ():
            if not isinstance(entry, Mapping):
                continue
            sym, name = entry.get("key"), entry.get("name")
            if (isinstance(sym, str) and _SYMBOL_RE.fullmatch(sym) and sym not in out
                    and isinstance(name, str) and name.strip() and len(name) <= 80):
                out[sym] = " ".join(name.split())
        if out:
            return out
    return out


def _referenced(output: Mapping[str, Any]) -> List[str]:
    """Every distinct symbol the post draws (`logo_refs`: the picture's logos in draw order, then the cover's)."""
    seen: List[str] = []
    refs = output.get("logo_refs")
    for ref in refs if isinstance(refs, list) else ():
        key = ref.get("key") if isinstance(ref, Mapping) else None
        if isinstance(key, str) and key not in seen:
            seen.append(key)
    return seen


def _record_companies(record: Any) -> Optional[List[str]]:
    """The distinct symbols a news record is ABOUT (duck-typed on the record's fields), or None when unreadable.
    A 13F spotlight is about its HOLDING (the filer is the subject of the sentence, never of the picture)."""
    try:
        series = getattr(record, "series", None)
        if series in ("insider_spotlight",):
            # owner decisions 2026-11-20: a list of 3-5 companies (never art: NEVER_ART_SERIES; kept so it reads)
            syms = [c.company.symbol for c in record.companies]
        elif series == "thirteen_f_spotlight":
            # every holding the post shows: a LIST (two to six, news-v23) is several companies — never art (it is not an
            # ART_SERIES member at all; kept so a stored record still reads)
            syms = [m.company.symbol for m in record.moves]
        elif series in ("ceo_buys", "insider_buys"):
            syms = [r.company.symbol for r in record.rows]
        elif series == "money_map":
            syms = [record.company.symbol]
        elif series == "congress_count":      # news-v23: 2-3 companies (never art: NEVER_ART_SERIES)
            syms = [c.company.symbol for c in record.counts]
        elif series == "earnings":            # news-v23: 2-4 companies (never art: NEVER_ART_SERIES)
            syms = [r.company.symbol for r in record.reports]
        else:
            return None
    except (AttributeError, TypeError):
        return None
    out: List[str] = []
    for s in syms:
        if not isinstance(s, str):
            return None
        if s not in out:
            out.append(s)
    return out


def _clean_fact(value: Any) -> Optional[str]:
    """A name-free fact as the brief may read it, or None: one short line of letters (never a digit)."""
    if not isinstance(value, str):
        return None
    text = " ".join(value[: FACT_MAX_CHARS * 4].split())
    if not text or len(text) > FACT_MAX_CHARS or not _FACT_RE.fullmatch(text) or "://" in text:
        return None
    return text


def _record_top_line(record: Any) -> Optional[str]:
    """The record's largest revenue line, when it carries one: a Money Map's largest segment (the one-issuer insider
    spotlight's `top_segment` was retired with it, owner decisions 2026-11-20). A NAME only, never its share or
    value."""
    try:
        if getattr(record, "series", None) == "money_map":
            segments = [s for s in record.segments if isinstance(getattr(s, "value_usd", None), (int, float))]
            if segments:
                return _clean_fact(max(segments, key=lambda s: s.value_usd).name)
    except (AttributeError, TypeError, ValueError):
        return None
    return None


def _filer_name(record: Any) -> Optional[str]:
    name = getattr(record, "filer_name", None) if getattr(record, "series", None) == "thirteen_f_spotlight" else None
    return name if isinstance(name, str) and name.strip() else None


def art_subject_for(series: Any, output: Any, record: Any = None) -> Optional[ArtSubject]:
    """The company a template post's art pictures, and where it may be drawn — or None (no art). Pure, never raises.

    Decision 4 (2026-11-15), narrowed by the owner decision of 2026-11-20 ("Drop it from insider posts
    (Recommended)": keep the code, use it NOWHERE for now): ART_SERIES is empty, so this answers None for every series
    today. The kept rules, for the day the owner turns art back on for a series: a COVER_ONLY_SERIES member (the
    Money Map was the last) → the cover only; no insider series ever (the insider list and the Form 4 weeks are 3-5
    companies), and no `feature` picture series is left (PICTURE_SERIES is empty). Never thirteen_f or thirteen_f_spotlight (news-v23: always a list of two to six holdings),
    congress_count or earnings (news-v23: always two or more companies), company_stakes, theme_explainer, a lesson or
    any multi-company roundup.

    The post must draw exactly one company (`logo_refs`), on a one-logo opening card; a spotlight's picture must be
    the `feature` layout headed by that company; and when the record is given, it must be about that company alone
    (a Form 4 roundup's rows, the 13F spotlight's holding). Anything that does not read back is None."""
    if not isinstance(series, str) or series not in ART_SERIES or not isinstance(output, Mapping):
        return None
    if output.get("series") != series or output.get("authorship") != "template":
        return None
    opening = output.get("opening_card")
    logos = opening.get("logos") if isinstance(opening, Mapping) else None
    if not isinstance(logos, list) or len(logos) != 1 or not isinstance(logos[0], str):
        return None
    symbol = logos[0]
    if not _SYMBOL_RE.fullmatch(symbol) or _referenced(output) != [symbol]:
        return None
    names = _logo_names(output)
    name = names.get(symbol)
    if name is None:
        return None
    if series in PICTURE_SERIES:
        spec = output.get("image_spec")
        header = spec.get("header") if isinstance(spec, Mapping) else None
        if (not isinstance(spec, Mapping) or spec.get("layout") != "feature" or not isinstance(header, Mapping)
                or header.get("logo") != symbol):
            return None
        surfaces: Tuple[str, ...] = (SURFACE_PICTURE, SURFACE_COVER)
    else:
        surfaces = (SURFACE_COVER,)
    if record is not None:
        if getattr(record, "series", None) != series or _record_companies(record) != [symbol]:
            return None
    top_line = _record_top_line(record) if record is not None else None
    others = [n for s, n in names.items() if s != symbol]
    filer = _filer_name(record) if record is not None else None
    banned = ban_terms(names=[name, *others, *([filer] if filer else []), *_markish_words(top_line)],
                       tickers=list(names))
    return ArtSubject(symbol=symbol, name=name, surfaces=surfaces, top_line=top_line, banned=banned)


# ── the subject sentence (the brief's answer) ─────────────────────────────────────────────────────


def _name_phrase(name: str) -> str:
    return " ".join(w for w in _WORD_RE.findall(name.lower()) if w not in _NAME_SUFFIXES)


def _markish_words(phrase: Optional[str]) -> List[str]:
    """The words of a revenue line that read as a product MARK ("iPhone", "AWS", "YouTube", "PlayStation"): an inner
    capital, or two or more capitals alone. A plain line ("Data Center", "Services") bans nothing."""
    out: List[str] = []
    for word in re.findall(r"[A-Za-z]+", phrase or ""):
        if (len(word) >= 2 and word.isupper()) or any(ch.isupper() for ch in word[1:]):
            out.append(word.lower())
    return out


def ban_terms(*, names: Iterable[Any] = (), tickers: Iterable[Any] = ()) -> Tuple[str, ...]:
    """What a subject may not contain: each company NAME without its corporate suffixes, lower-case ("Meta Platforms
    Inc" → "meta platforms"), matched as a whole phrase against the folded subject; each TICKER, upper-case and
    case-SENSITIVE (and a dash form's root: "BRK-B" → "BRK"), so "ON" or "LOW" never refuses the words "on" or
    "low". A name phrase under MIN_NAME_BAN_CHARS ("HP Inc." → "hp", "3M Company" → "3m") joins the way a ticker
    does — upper-case and case-SENSITIVE ("HP", "3M") — so the brand is refused while "a 3-hp motor" still passes. A
    one-letter ticker or name, and a digit-only name, are left out (cover_art's reasoning: the article "A" opens most
    subjects). Deterministic, de-duplicated."""
    out: List[str] = []
    for raw in names or ():
        if isinstance(raw, str):
            phrase = _name_phrase(raw)
            if len(phrase) < MIN_NAME_BAN_CHARS:
                phrase = phrase.upper()
                if len(phrase) < MIN_TICKER_BAN_CHARS or not phrase.isupper():
                    continue
            if phrase not in out:
                out.append(phrase)
    for raw in tickers or ():
        if not isinstance(raw, str) or not _SYMBOL_RE.fullmatch(raw.strip().upper()):
            continue
        sym = raw.strip().upper()
        for t in (sym, sym.split("-")[0]):
            if len(t) >= MIN_TICKER_BAN_CHARS and t not in out:
                out.append(t)
    return tuple(out)


def clean_subject(raw: Any) -> str:
    """The answer's subject as one line, outer quotes stripped."""
    if not isinstance(raw, str):
        return ""
    return " ".join(raw[: SUBJECT_MAX_CHARS * 4].split()).strip().strip('"“”').strip()


def subject_problem(text: Any, banned: Sequence[str] = ()) -> Optional[str]:
    """Why `text` cannot be a post's art subject, or None: the length cap FIRST (before any regex), then the Money
    Moves floor (`style.subject_problem` on the blueprints palette: length, the brand / person deny-list), then the
    company's own names and tickers (`ban_terms`), then the marketing deny-list (_ART_SUBJECT_BANNED_RE: charts,
    money, politics, verdict and alarm imagery). Pure; linear."""
    if not isinstance(text, str):
        return f"not a sentence ({type(text).__name__})"
    if len(text) > SUBJECT_MAX_CHARS:
        return f"too long ({len(text)} characters)"
    problem = style.subject_problem(text, PALETTE)
    if problem is not None:
        return problem
    folded = " ".join(_WORD_RE.findall(text.lower()))
    for term in banned:
        if not isinstance(term, str) or not term:
            continue
        haystack = text if term.isupper() else folded
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", haystack):
            return f"{_COMPANY_NAMED} ({term!r})"
    hit = _ART_SUBJECT_BANNED_RE.search(text)
    if hit:
        return f"names {hit.group(0).lower()!r}"
    return None


# ── prompts ───────────────────────────────────────────────────────────────────────────────────────

#: Appended (never inserted) to the Money Moves prompt: `style.prompt_for`'s own output is unchanged.
MARKETING_ADDENDUM = (
    "FOR THIS PICTURE, ALSO: no chart, graph, arrow, candlestick, stock ticker or data display of any kind, and any "
    "screen in the picture is blank and dark. No money: no coins, banknotes, cash or currency symbols. No flags, "
    "government buildings or political symbols. Nothing broken, cracked, burning, falling or rising, and no "
    "trophies, crowns, medals or rockets. A calm, neutral, unremarkable portrait of the object — neither "
    "celebratory nor gloomy."
)

#: The brief's system instruction (internal: its answer is never shown publicly; the owner reads it in Telegram).
BRIEF_SYSTEM = (
    "You choose the subject of one still-life picture for a financial education brand. You reply with JSON only."
)
#: The check's system instruction.
CHECK_SYSTEM = (
    "You inspect one picture before it is published by a financial education brand. You are strict: when unsure, "
    "you answer true. You reply with JSON only."
)

_BRIEF_BODY = """You are choosing the SUBJECT of one still-life picture that will sit beside a short, factual post about a public company. The picture is decoration only: it must say nothing about the company's news, its results, its stock or its future.

{facts}

Describe ONE single physical object (or a small group of at most three objects) that this business itself makes, sells, runs on or works with — its own generic, everyday thing. Follow the shape of these examples exactly:
{examples}

HARD RULES, all of them:
- A real physical object that exists and can be photographed. Never a scene, a place, a diagram or an abstract idea.
- Never name this company, any other company, a product line, a brand or any real person, and never use a word of the company's name for the object. Describe the generic category and say it is plain and unbranded.
- The object carries no writing: say it is "blank" or "unmarked".
- Never mention anything below, not even to say it is absent: people, faces or hands; money, coins, banknotes, cash, currency or price tags; charts, graphs, arrows, screens showing data or stock tickers; bulls or bears; flags, government buildings or political symbols; anything rising, falling, soaring, broken, cracked, burning or collapsing; trophies, crowns, medals, rockets, storms or skulls.
- For a bank, insurer, exchange, payment network or asset manager, choose a calm object of the trade that is not money: a blank payment card, a bank vault door, a safe-deposit box, a plain office tower, a blank ledger binder.
- Describe only the object and how it sits — not the lighting, background, colour, mood or camera.
- One sentence, under 55 words.

Then choose the TREATMENT, the picture's visual style, as one of these ids:
{treatments}

Reply with JSON only: {{"subject": "<the sentence>", "treatment": "<one id>"}}."""

_CHECK_BODY = """You are inspecting ONE picture before it is published beside a factual post about a public company. It was meant to show: {subject}

Look at the whole picture carefully — every corner, edge, surface, screen, card and label. Answer each question with true or false:
- text_or_numbers: is there ANY text, letter, number, digit, character, signature, label or legible marking anywhere?
- logo_or_brand: is there any logo, brand mark, trademark, emblem, a real company's recognisable product design, or a real brand's packaging?
- people_faces_or_hands: is there any person, face, hand, body part or human figure — including a statue, reflection or silhouette?
- chart_money_or_flag: is there any chart, graph, arrow, candlestick, data display, stock ticker, coin, banknote, cash, currency symbol, flag or government building?
- alarming_or_judgemental: does anything read as good or bad news — broken, cracked, burning, collapsing, falling or rising, celebratory or gloomy, a trophy, crown, medal, rocket, storm or skull?
- shows_the_subject: is the object described above clearly shown and immediately recognisable?
- notes: at most 200 characters naming what you saw when any of the first five is true or the object is not recognisable; otherwise an empty string.

When unsure about any of the first five, answer true. Reply with JSON only."""

#: The brief's answer (Gemini schema dialect; ClaudeOnly translates it).
BRIEF_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "subject": {"type": "STRING"},
        "treatment": {"type": "STRING", "enum": list(MARKETING_TREATMENTS)},
    },
    "required": ["subject", "treatment"],
}
#: The check's answer.
CHECK_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        **{flag: {"type": "BOOLEAN"} for flag in CHECK_FLAGS},
        "shows_the_subject": {"type": "BOOLEAN"},
        "notes": {"type": "STRING"},
    },
    "required": [*CHECK_FLAGS, "shows_the_subject", "notes"],
}


def _nonce(run_id: Any, ref: Any) -> str:
    """A short id of (run, ledger ref) for the request line — the ref carries a ticker, so only its hash is sent."""
    return hashlib.sha1(f"{run_id}|{ref}".encode("utf-8")).hexdigest()[:12]


#: The Money Moves catalog rows whose subjects the brief shows as examples (by slug: their text is READ from
#: `style.ARTICLES`, never copied). Hand-picked from the blueprints rows for calm, generic objects of a business —
#: never the catalog's first row (a coin: money, which this module bans) or the cigar butt (a person's habit).
BRIEF_EXAMPLE_SLUGS: Tuple[str, ...] = (
    "apples-services-revolution", "costcos-membership-magic", "microsofts-cloud-metamorphosis",
)


def _brief_examples() -> List[str]:
    """The example subjects (BRIEF_EXAMPLE_SLUGS, read from the catalog): only a blueprints row that still passes
    this module's own subject rules — a catalog edit can drop an example, never smuggle a banned one in."""
    out: List[str] = []
    for slug in BRIEF_EXAMPLE_SLUGS:
        row = style.ARTICLES.get(slug)
        if row and row[0] == PALETTE and subject_problem(row[2]) is None:
            out.append(row[2])
    return out


def brief_facts(subject: ArtSubject, facts: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
    """The name-free facts the brief reads (decision 3): the company's display name, its sector and industry (from
    `facts`, the profile's two words) and its top revenue line (the record's, else `facts["top_line"]`) — each a
    short plain name with no digit, dropped when it is not one. Never a figure, a person, a ticker, the filing, the
    trade or the headline."""
    out: Dict[str, str] = {"company": subject.name}
    given = facts if isinstance(facts, Mapping) else {}
    for key in ("sector", "industry"):
        value = _clean_fact(given.get(key))
        if value:
            out[key] = value
    top = subject.top_line or _clean_fact(given.get("top_line"))
    if top:
        out["top_line"] = top
    return out


def brief_prompt(*, run_id: Any, ref: Any, inputs: Mapping[str, str], attempt: int,
                 problem: Optional[str] = None) -> str:
    """The brief request: a nonce line (run + the ref's hash + the attempt), the facts, the rules, the treatments
    — plus, on the retry, why the last subject was refused (code words and the checked text's own match only)."""
    labels = (("company", "COMPANY"), ("sector", "SECTOR"), ("industry", "INDUSTRY"),
              ("top_line", "LARGEST REVENUE LINE"))
    facts = "\n".join(f"{label}: {inputs[key]}" for key, label in labels if inputs.get(key))
    treatments = "\n".join(f"- {t}: {style.TREATMENTS[t][0]}" for t in MARKETING_TREATMENTS)
    examples = "\n".join(f"- {s}" for s in _brief_examples())
    text = (f"REQUEST post-art {_fold(str(run_id), 64)} {_nonce(run_id, ref)} attempt {int(attempt)}\n\n"
            + _BRIEF_BODY.format(facts=facts, examples=examples, treatments=treatments))
    if problem:
        text += (f"\n\nYOUR LAST ANSWER WAS REFUSED ({_fold(problem, 120)}). Write a new subject that follows every "
                 "rule above.")
    return text


def image_prompt(subject: str, treatment: str) -> str:
    """The drawing prompt: `style.prompt_for(subject, "blueprints", treatment)` byte for byte, then the marketing
    addendum."""
    return style.prompt_for(subject, PALETTE, treatment) + "\n\n" + MARKETING_ADDENDUM


def redraw_prompt(prompt: str, flags: Sequence[str]) -> str:
    """The second drawing's prompt: the first one plus the check's flags in FIXED words (FLAG_WORDS) — never the
    checker's notes. Unknown flags are ignored; no known flag → the prompt unchanged."""
    words = [FLAG_WORDS[f][0] for f in (*CHECK_FLAGS, NOT_RECOGNISABLE) if f in set(flags)]
    if not words:
        return prompt
    return (f"{prompt}\n\nA FIRST ATTEMPT WAS REFUSED because it showed {', '.join(words)}. Draw it again without "
            "any of that.")


def check_prompt(*, run_id: Any, ref: Any, subject: str, attempt: int) -> str:
    return (f"REQUEST post-art-check {_fold(str(run_id), 64)} {_nonce(run_id, ref)} attempt {int(attempt)}\n\n"
            + _CHECK_BODY.format(subject=_fold(subject, SUBJECT_MAX_CHARS)))


def prompt_sha1(prompt: str) -> str:
    """sha1(prompt)[:12] — the Money Moves manifests' form."""
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]


def treatment_words(treatment: Any) -> str:
    """A treatment's short human name (the style's own), for the review line; "" for an unknown one."""
    entry = style.TREATMENTS.get(treatment) if isinstance(treatment, str) else None
    return entry[0] if entry else ""


# ── answers ───────────────────────────────────────────────────────────────────────────────────────


def _json_object(text: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str) or len(text) > 20_000:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_brief(text: Any) -> Tuple[Optional[str], Optional[str]]:
    """(the cleaned subject, the treatment as answered) of a brief answer; (None, None) when it is not a JSON
    object with a string subject."""
    obj = _json_object(text)
    if obj is None or not isinstance(obj.get("subject"), str):
        return None, None
    treatment = obj.get("treatment")
    return clean_subject(obj["subject"]), treatment if isinstance(treatment, str) else None


@dataclass(frozen=True)
class Verdict:
    passed: bool
    flags: Tuple[str, ...]
    notes: str


def parse_verdict(text: Any) -> Optional[Verdict]:
    """The check's verdict, FAIL CLOSED: None (unreadable) unless the answer is a JSON object whose five flags and
    `shows_the_subject` are all real booleans. It passes only when every flag is false and the subject shows.
    `notes` is folded and capped (Telegram only); a missing or non-string note is ""."""
    obj = _json_object(text)
    if obj is None:
        return None
    values = [obj.get(flag) for flag in (*CHECK_FLAGS, "shows_the_subject")]
    if any(not isinstance(v, bool) for v in values):
        return None
    flags = [flag for flag in CHECK_FLAGS if obj[flag] is True]
    if obj["shows_the_subject"] is not True:
        flags.append(NOT_RECOGNISABLE)
    return Verdict(passed=not flags, flags=tuple(flags), notes=_fold(obj.get("notes"), NOTES_MAX_CHARS))


# ── storage, the stored record, the worker's read-back ────────────────────────────────────────────


def art_path(sha256: str) -> str:
    """`art/<sha256[:32]>.jpg` — the object's key in the media bucket."""
    return f"{ART_PREFIX}/{sha256[:32]}.{ART_EXT}"


def expected_art_url(sha256: str) -> str:
    """The unsigned public URL of the art object — built from settings at call time, exactly as
    `run_service.MarketingRunService.public_url(art_path(sha))` builds it (pinned equal; no import of run_service,
    which imports this module)."""
    base = f"{str(settings.SUPABASE_URL).rstrip('/')}/storage/v1/object/public/{settings.MARKETING_MEDIA_BUCKET}"
    return f"{base}/{art_path(sha256)}"


def _plain_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def art_problem(art: Any) -> Optional[str]:
    """Why a stored `ok` art record cannot be drawn, or None: a known version, a 64-hex sha256, the url this deploy
    would build for it, bytes 1..ART_MAX_BYTES, width ART_MIN_WIDTH..ART_MAX_WIDTH, a height within
    ART_ASPECT_TOLERANCE of 16:9. Pure (the URL reads settings)."""
    if not isinstance(art, Mapping):
        return f"not an object ({type(art).__name__})"
    if art.get("status") != STATUS_OK:
        return "not ok"
    if art.get("version") not in ART_VERSIONS:
        return f"unknown version {str(art.get('version'))[:20]!r}"
    sha = art.get("sha256")
    if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
        return "no sha256"
    if art.get("url") != expected_art_url(sha):
        return "url is not the art object's public url"
    size, width, height = _plain_int(art.get("bytes")), _plain_int(art.get("width")), _plain_int(art.get("height"))
    if size is None or not 1 <= size <= ART_MAX_BYTES:
        return f"bytes {art.get('bytes')!r} outside 1..{ART_MAX_BYTES}"
    if width is None or not ART_MIN_WIDTH <= width <= ART_MAX_WIDTH:
        return f"width {art.get('width')!r} outside {ART_MIN_WIDTH}..{ART_MAX_WIDTH}"
    if height is None or height < 1 or abs(width / height - ART_ASPECT) > ART_ASPECT_TOLERANCE:
        return f"{width}x{art.get('height')!r} is not 16:9"
    return None


#: The keys the worker receives (decision 11) — never the models, the subject, the checks or the usage.
WORKER_ART_KEYS: Tuple[str, ...] = ("url", "sha256", "bytes", "width", "height")


def drawable_art(output: Any) -> Optional[Dict[str, Any]]:
    """{url, sha256, bytes, width, height} of a template output's art the worker may draw (and the server's
    declaration check compares against), or None: no art, a failed record (normal), a lesson or a series that
    never carries art (ERROR — something wrote art where none belongs), or an `ok` record that does not validate
    (`art_problem`, ERROR). Pure apart from the log line."""
    if not isinstance(output, Mapping) or output.get("art") is None:
        return None
    art = output.get("art")
    if isinstance(art, Mapping) and art.get("status") != STATUS_OK:
        return None
    if output.get("authorship") != "template" or output.get("series") not in ART_SERIES:
        logger.error("post art: an output of series %r (authorship %r) carries art — never drawn",
                     str(output.get("series"))[:40], str(output.get("authorship"))[:20])
        return None
    problem = art_problem(art)
    if problem is not None:
        logger.error("post art: the %s output's art record says ok but cannot be drawn (%s) — not drawn",
                     output.get("series"), problem)
        return None
    return {k: art[k] for k in WORKER_ART_KEYS}


def worker_art(output: Any) -> Optional[Dict[str, Any]]:
    """What the worker's read-back carries as `art`: `drawable_art(output)` while MARKETING_ART_ENABLED is true —
    read at CALL time, so turning the switch off stops new drawing at the next render without a deploy (the server
    accepts a missing declaration: art is never required) — else None."""
    if settings.MARKETING_ART_ENABLED is not True:
        return None
    return drawable_art(output)


# ── prices (decision 10) ──────────────────────────────────────────────────────────────────────────


def _rates(model: Any) -> Optional[Tuple[Optional[int], ...]]:
    """digest_service's Claude price row for `model` (micro-USD per 1M tokens, by kind), or None."""
    if not isinstance(model, str) or not model.strip():
        return None
    from app.services.marketing import digest_service

    return digest_service.claude_rates(model.strip())


def claude_call_worst_micros(model: Any) -> Optional[int]:
    """One Claude art call at its worst: CLAUDE_WORST_INPUT_TOKENS input + CAYDEX_CLAUDE_MAX_TOKENS output (thinking
    included), rounded UP; None when the model has no input or output price."""
    rates = _rates(model)
    if rates is None or rates[0] is None or rates[1] is None:
        return None
    numerator = CLAUDE_WORST_INPUT_TOKENS * rates[0] + int(settings.CAYDEX_CLAUDE_MAX_TOKENS) * rates[1]
    return -(-numerator // 1_000_000)


def _image_micros(value: Any = None) -> int:
    v = _plain_int(value)
    return v if v is not None and v > 0 else int(settings.MARKETING_ART_IMAGE_MICROS)


def run_worst_micros(model: Any = None, image_micros: Any = None) -> Optional[int]:
    """The worst ONE art run can spend: ART_DRAWS × IMAGE_MAX_ATTEMPTS images + ART_CLAUDE_CALLS Claude calls at
    `claude_call_worst_micros` — the reserve the monthly cap adds before a run starts. None when the Claude model
    (MARKETING_ART_CLAUDE_MODEL by default) has no price (the caller fails closed)."""
    per_call = claude_call_worst_micros(settings.MARKETING_ART_CLAUDE_MODEL if model is None else model)
    if per_call is None:
        return None
    return ART_DRAWS * IMAGE_MAX_ATTEMPTS * _image_micros(image_micros) + ART_CLAUDE_CALLS * per_call


def estimated_micros(usage: Any) -> Optional[int]:
    """The micro-USD a recorded `usage` stands for: (images + images_uncertain) × its `image_micros` (else today's
    setting) + every Claude token at its model's rates (rounded up) + every uncertain Claude call at
    `claude_call_worst_micros`. None when the usage cannot be read or a model / kind with tokens has no price —
    the cap then fails closed."""
    if not isinstance(usage, Mapping):
        return None
    images, uncertain = _plain_int(usage.get("images", 0)), _plain_int(usage.get("images_uncertain", 0))
    if images is None or uncertain is None or images < 0 or uncertain < 0:
        return None
    total = (images + uncertain) * _image_micros(usage.get("image_micros"))
    claude = usage.get("claude") or {}
    unsure = usage.get("claude_uncertain") or {}
    if not isinstance(claude, Mapping) or not isinstance(unsure, Mapping):
        return None
    numerator = 0
    kinds = ("input", "output", "cache_read", "cache_write")
    for model, counts in claude.items():
        rates = _rates(model)
        if not isinstance(counts, Mapping) or set(counts) - set(kinds):
            return None
        for kind, rate in zip(kinds, rates or (None,) * 4):
            n = _plain_int(counts.get(kind, 0))
            if n is None or n < 0:
                return None
            if n and rate is None:
                return None
            numerator += n * (rate or 0)
    total += -(-numerator // 1_000_000)
    for model, n in unsure.items():
        count = _plain_int(n)
        if count is None or count < 0:
            return None
        if count:
            per_call = claude_call_worst_micros(model)
            if per_call is None:
                return None
            total += count * per_call
    return total


# ── preflight (no call made) ──────────────────────────────────────────────────────────────────────


def claude_model_problem() -> Optional[str]:
    """`claude_unpriced` when MARKETING_ART_CLAUDE_MODEL (read now) is blank or has no price in digest_service's Claude
    table, else None — ONE ERROR per value per process (decision 9), the once-key shared by `preflight_problem` and
    `script_service._attach_art`'s cap gate, so the two never log the same bad setting twice."""
    model = str(settings.MARKETING_ART_CLAUDE_MODEL or "").strip()
    if not model or claude_call_worst_micros(model) is None:
        _error_once(f"claude:{model}", "post art: MARKETING_ART_CLAUDE_MODEL=%s is blank or has no price in "
                    "digest_service's Claude table — no art (the cap could not price it)",
                    _shown(settings.MARKETING_ART_CLAUDE_MODEL))
        return "claude_unpriced"
    return None


def preflight_problem() -> Optional[str]:
    """Why no art can be made right now, before any call or any spend: an image model that is blank or moves
    (`image_model_invalid`), a Claude model without a price (`claude_unpriced`), no key or an open shared breaker
    (`claude_unavailable`). A bad setting is an ERROR once per value. Read at call time."""
    image = str(settings.MARKETING_ART_IMAGE_MODEL or "").strip()
    if not image or not _MODEL_RE.fullmatch(image) or _MOVING_MODEL_RE.search(image):
        _error_once(f"image:{image}", "post art: MARKETING_ART_IMAGE_MODEL=%s is blank, malformed or a preview / "
                    "latest model — no art", _shown(settings.MARKETING_ART_IMAGE_MODEL))
        return "image_model_invalid"
    problem = claude_model_problem()
    if problem is not None:
        return problem
    from app.integrations import anthropic_client as ac
    from app.services import llm_provider as lp

    if not ac.is_configured():
        _error_once("nokey", "post art: CAYDEX_ANTHROPIC_API_KEY is not set — no art")
        return "claude_unavailable"
    if lp.breaker_open():
        logger.warning("post art: the shared Claude breaker is open — no art this run")
        return "claude_unavailable"
    return None


# ── the picture (Pillow, off the event loop) ──────────────────────────────────────────────────────


class _Halt:
    """Holds the art run's internal control-flow exception. NESTED on purpose (the `template_onscreen._Walk.Invalid`
    precedent): it never leaves `make_post_art`, so it is not a module-level marketing exception class and needs no
    classify_exception branch (tests/test_marketing_script_flow.py walks every module-level one)."""

    class Stop(Exception):
        """A step ended the run: `reason` is one of ART_REASONS, `detail` says why (for the log line)."""

        def __init__(self, reason: str, detail: str = "") -> None:
            super().__init__(f"{reason}: {detail}" if detail else reason)
            self.reason = reason
            self.detail = detail


def normalise_art(blob: bytes) -> Tuple[bytes, int, int]:
    """(the JPEG to check and store, its width, its height) from the image model's bytes. Header first: a PNG or
    JPEG of one frame, at least ART_MIN_SOURCE_WIDTH wide, at most ART_MAX_SOURCE_PIXELS (never decoded when
    over), within ART_SOURCE_ASPECT_TOLERANCE of 16:9; then decoded in full (a truncated body raises), fitted to
    ART_WIDTH × ART_HEIGHT (`style.fit_to`), encoded as the Money Moves master (`style.save_master`, q95) — at
    ART_FALLBACK_QUALITY when that is over ART_MAX_BYTES — and decoded back in full. Raises
    `_Halt.Stop("image_invalid")` on anything else. Deterministic for equal bytes. CPU work: call it in a thread."""
    from PIL import Image

    if not isinstance(blob, (bytes, bytearray)) or not blob:
        raise _Halt.Stop("image_invalid", "no bytes")
    try:
        with Image.open(io.BytesIO(bytes(blob))) as im:
            w, h = im.size
            if im.format not in ART_SOURCE_FORMATS:
                raise _Halt.Stop("image_invalid", f"format {im.format!r}")
            if getattr(im, "n_frames", 1) != 1:
                raise _Halt.Stop("image_invalid", "more than one frame")
            if w * h > ART_MAX_SOURCE_PIXELS:
                raise _Halt.Stop("image_invalid", f"{w}x{h} is over {ART_MAX_SOURCE_PIXELS} pixels")
            if w < ART_MIN_SOURCE_WIDTH or h < 1 or abs((w / h) / ART_ASPECT - 1) > ART_SOURCE_ASPECT_TOLERANCE:
                raise _Halt.Stop("image_invalid", f"{w}x{h} is not a 16:9 picture at least {ART_MIN_SOURCE_WIDTH} wide")
            im.load()
            fitted, _cropped = style.fit_to(im, ART_WIDTH, ART_HEIGHT)
    except _Halt.Stop:
        raise
    except Exception as e:  # noqa: BLE001 — Pillow's own errors: not an image, truncated, unsupported
        raise _Halt.Stop("image_invalid", f"decode: {type(e).__name__}: {str(e)[:120]}") from e
    buf = io.BytesIO()
    style.save_master(fitted, buf)
    data = buf.getvalue()
    if len(data) > ART_MAX_BYTES:
        buf = io.BytesIO()
        fitted.save(buf, "JPEG", quality=ART_FALLBACK_QUALITY, optimize=True)
        data = buf.getvalue()
    if len(data) > ART_MAX_BYTES:
        raise _Halt.Stop("image_invalid", f"{len(data)} bytes after re-encoding (cap {ART_MAX_BYTES})")
    try:
        with Image.open(io.BytesIO(data)) as check:
            check.verify()
        with Image.open(io.BytesIO(data)) as check:
            check.load()
            if check.format != "JPEG" or check.size != (ART_WIDTH, ART_HEIGHT) or check.mode != "RGB":
                raise _Halt.Stop("image_invalid", f"re-encoded as {check.format} {check.size} {check.mode}")
    except _Halt.Stop:
        raise
    except Exception as e:  # noqa: BLE001 — the encoder's own output must decode; anything else is a bug upstream
        raise _Halt.Stop("image_invalid", f"re-decode: {type(e).__name__}: {str(e)[:120]}") from e
    return data, ART_WIDTH, ART_HEIGHT


# ── one art run ───────────────────────────────────────────────────────────────────────────────────

#: The clock (monkeypatched by tests).
_mono = time.monotonic


def _gemini_client() -> Any:
    from app.integrations.gemini import get_gemini_client

    return get_gemini_client()


class _ArtRun:
    """One art run's state: the deadline, every call made, and the record under construction."""

    def __init__(self, *, run_id: str, ref: str, series: str, subject: Optional[ArtSubject], budget: float,
                 gemini: Any, claude: Any) -> None:
        self.run_id, self.ref, self.series, self.subject = run_id, ref, series, subject
        self.started = _mono()
        self.deadline = self.started + budget
        self._gemini, self._claude_client = gemini, claude
        self.claude_model = str(settings.MARKETING_ART_CLAUDE_MODEL or "").strip()
        self.image_model = str(settings.MARKETING_ART_IMAGE_MODEL or "").strip()
        self.image_micros = int(settings.MARKETING_ART_IMAGE_MICROS)
        self.images = 0
        self.images_uncertain = 0
        self.claude_usage: Dict[str, Dict[str, int]] = {}
        self.claude_uncertain: Dict[str, int] = {}
        self.served: Dict[str, Optional[str]] = {"brief": None, "check": None}
        self.briefs = 0
        self.attempts = 0
        self.checks: List[Dict[str, Any]] = []
        self.facts: Dict[str, str] = {}
        self.subject_text: Optional[str] = None
        self.treatment: Optional[str] = None
        self.treatment_source: Optional[str] = None
        self.prompt_sha1: Optional[str] = None
        self.redraw_prompt_sha1: Optional[str] = None
        self.stored: Optional[Dict[str, Any]] = None

    # ── time ──

    def left(self) -> float:
        return self.deadline - _mono()

    def slice(self, step: str, follow: Sequence[str]) -> float:
        """Seconds this step may take: min(its cap, what is left − the minimums of `follow`); `_Halt.Stop("timeout")`
        when that is under its own minimum."""
        lo, cap = STEP_SECONDS[step]
        room = self.left() - sum(STEP_SECONDS[s][0] for s in follow)
        if room < lo:
            needed = lo + sum(STEP_SECONDS[s][0] for s in follow)
            raise _Halt.Stop("timeout", f"{step}: {max(self.left(), 0.0):.1f}s left, {needed:.0f}s needed")
        return min(cap, room)

    def fits(self, steps: Sequence[str]) -> bool:
        return self.left() >= sum(STEP_SECONDS[s][0] for s in steps)

    # ── Claude ──

    def _claude(self) -> Any:
        from app.services import llm_provider as lp

        return lp.ClaudeOnly(self.claude_model, self._claude_client, breaker=lp.BREAKER_ACCOUNT)

    def _uncertain(self, model: str) -> None:
        self.claude_uncertain[model] = self.claude_uncertain.get(model, 0) + 1

    async def ask(self, kind: str, prompt: str, *, schema: Dict[str, Any], system: str, tag: str,
                  allotted: float, images: Sequence[Tuple[bytes, str]] = ()) -> str:
        """One Claude call under `allotted` seconds (the client's own deadline CLAUDE_CLIENT_MARGIN_SECONDS sooner).
        Returns the answer's text; every token it billed (or may have billed) is recorded. `_Halt.Stop` otherwise."""
        from app.integrations import anthropic_client as ac
        from app.services import llm_provider as lp

        model = self.claude_model
        # always strictly inside the slice, so the client's typed timeout ends the call before our own cut-off does
        client_deadline = max(allotted - CLAUDE_CLIENT_MARGIN_SECONDS, allotted / 2)
        try:
            result = await asyncio.wait_for(
                self._claude().generate_json(
                    prompt, system_instruction=system, response_schema=schema, usage_tag=tag,
                    images=list(images), max_tokens=int(settings.CAYDEX_CLAUDE_MAX_TOKENS),
                    deadline_seconds=client_deadline, effort=ART_EFFORT),
                timeout=allotted)
        except asyncio.TimeoutError:
            self._uncertain(model)     # cut off from outside: it may have billed, nothing says what
            raise _Halt.Stop("timeout", f"{kind}: no answer in {allotted:.0f}s") from None
        except ac.ClaudeException as e:
            self.claude_usage = lp.merge_claude_usage(self.claude_usage, lp.claude_usage_of(e))
            if getattr(e, "billing_unknown", False):
                self._uncertain(model)
            raise _Halt.Stop(self._claude_reason(kind, e), f"{kind}: {type(e).__name__}: {str(e)[:160]}") from e
        except Exception as e:  # noqa: BLE001 — a bug on the Claude path: counted at its worst, never a raise
            self._uncertain(model)
            logger.exception("post art: the %s call raised %s run_id=%s", kind, type(e).__name__, self.run_id)
            raise _Halt.Stop("internal_error", f"{kind}: {type(e).__name__}") from e
        self.claude_usage = lp.merge_claude_usage(self.claude_usage, lp.claude_usage_of(result))
        self.served[kind] = str(result.get("model") or model)[:64] if isinstance(result, Mapping) else model
        text = result.get("text") if isinstance(result, Mapping) else None
        return text if isinstance(text, str) else ""

    @staticmethod
    def _claude_reason(kind: str, e: Exception) -> str:
        from app.integrations import anthropic_client as ac

        if isinstance(e, ac.ClaudeRequestInvalidException):
            logger.error("post art: the %s request was refused before sending (%s) — a bug here", kind, e)
            return "internal_error"
        if isinstance(e, ac.ClaudeTimeoutException):
            return "timeout"
        if isinstance(e, (ac.ClaudeUnavailableException, ac.ClaudeRateLimitException, ac.ClaudeConfigException,
                          ac.ClaudeCreditsExhaustedException, ac.ClaudeAuthException)):
            return "claude_unavailable"
        return "brief_unusable" if kind == "brief" else "check_unreadable"

    # ── the steps ──

    async def load_facts(self, facts: Optional[Mapping[str, Any]],
                         facts_fn: Optional[Callable[[str], Awaitable[Any]]]) -> None:
        got: Any = facts
        if got is None and facts_fn is not None and self.subject is not None:
            lo, cap = STEP_SECONDS["facts"]
            room = self.left() - sum(STEP_SECONDS[s][0] for s in HAPPY_PATH)
            if room < lo:
                logger.info("post art: no time for the company facts run_id=%s — the brief goes without them",
                            self.run_id)
            else:
                try:
                    got = await asyncio.wait_for(facts_fn(self.subject.symbol), timeout=min(cap, room))
                except asyncio.TimeoutError:
                    logger.warning("post art: the company facts of %s took over %.0fs run_id=%s — the brief goes "
                                   "without them", self.subject.symbol, min(cap, room), self.run_id)
                    got = None
                except Exception as e:  # noqa: BLE001 — optional context, never a failure
                    logger.warning("post art: the company facts of %s failed run_id=%s (%s: %s) — the brief goes "
                                   "without them", self.subject.symbol, self.run_id, type(e).__name__, str(e)[:160])
                    got = None
        self.facts = brief_facts(self.subject, got) if self.subject is not None else {}

    async def brief(self) -> None:
        """(a): a usable subject + treatment; one more brief only when the first subject is unusable."""
        problem: Optional[str] = None
        for attempt in (1, 2):
            if attempt == 2 and not self.fits(HAPPY_PATH):
                # the cause is the unusable first subject; the clock only stopped its one retry
                raise _Halt.Stop("brief_unusable", f"no time for a second brief after: {problem}")
            allotted = self.slice("brief", HAPPY_PATH[1:])
            self.briefs += 1
            # the retry names what was wrong — but never echoes a ticker or name back (decision 3: no ticker in a brief)
            note = (_COMPANY_NAMED if problem is not None and problem.startswith(_COMPANY_NAMED) else problem)
            text = await self.ask(
                "brief", brief_prompt(run_id=self.run_id, ref=self.ref, inputs=self.facts, attempt=attempt,
                                      problem=note),
                schema=BRIEF_SCHEMA, system=BRIEF_SYSTEM, tag=BRIEF_USAGE_TAG, allotted=allotted)
            subject, treatment = parse_brief(text)
            problem = ("is not a JSON answer with a subject" if subject is None
                       else subject_problem(subject, self.subject.banned if self.subject else ()))
            if problem is None:
                self.subject_text = subject
                if treatment in MARKETING_TREATMENTS:
                    self.treatment, self.treatment_source = treatment, "brief"
                else:
                    self.treatment = style.default_treatment(f"{self.run_id}:{self.ref}", PALETTE)
                    self.treatment_source = "default"
                    logger.warning("post art: the brief chose treatment %r outside the allow-list run_id=%s — "
                                   "using %s", str(treatment)[:40], self.run_id, self.treatment)
                return
            logger.warning("post art: brief %d's subject is unusable run_id=%s series=%s: %s", attempt,
                           self.run_id, self.series, problem)
        raise _Halt.Stop("brief_unusable", f"two briefs, both unusable: {problem}")

    async def draw(self, prompt: str) -> Tuple[bytes, int, int]:
        """(b) + (c): one image call, then the normalised JPEG."""
        from app.integrations.gemini import GeminiImageError, GeminiImageRefusedError

        allotted = self.slice("image", HAPPY_PATH[2:])
        gemini = self._gemini if self._gemini is not None else _gemini_client()
        self.attempts += 1
        try:
            blob = await asyncio.wait_for(
                gemini.generate_image(prompt, model_name=self.image_model, aspect_ratio=IMAGE_ASPECT_RATIO,
                                      image_size=IMAGE_SIZE, max_attempts=IMAGE_MAX_ATTEMPTS,
                                      backoff_seconds=IMAGE_BACKOFF_SECONDS, usage_tag=IMAGE_USAGE_TAG),
                timeout=allotted)
        except asyncio.TimeoutError:
            self.images_uncertain += IMAGE_MAX_ATTEMPTS
            raise _Halt.Stop("timeout", f"image: no picture in {allotted:.0f}s") from None
        except GeminiImageRefusedError as e:
            self.images_uncertain += IMAGE_MAX_ATTEMPTS
            raise _Halt.Stop("image_refused", f"{type(e).__name__}: {str(e)[:160]}") from e
        except GeminiImageError as e:
            self.images_uncertain += IMAGE_MAX_ATTEMPTS
            raise _Halt.Stop("image_unavailable", f"{type(e).__name__}: {str(e)[:160]}") from e
        except Exception as e:  # noqa: BLE001 — an unexpected client error: no picture, counted at its worst
            self.images_uncertain += IMAGE_MAX_ATTEMPTS
            logger.exception("post art: the image call raised %s run_id=%s", type(e).__name__, self.run_id)
            raise _Halt.Stop("image_unavailable", f"{type(e).__name__}") from e
        self.images += 1
        allotted = self.slice("normalise", HAPPY_PATH[3:])
        try:
            return await asyncio.wait_for(asyncio.to_thread(normalise_art, blob), timeout=allotted)
        except asyncio.TimeoutError:
            raise _Halt.Stop("timeout", f"normalise: not done in {allotted:.0f}s") from None

    async def check(self, jpeg: bytes) -> Verdict:
        """(d): the vision check of the exact bytes to be stored."""
        allotted = self.slice("check", HAPPY_PATH[4:])
        text = await self.ask(
            "check", check_prompt(run_id=self.run_id, ref=self.ref, subject=self.subject_text or "",
                                  attempt=len(self.checks) + 1),
            schema=CHECK_SCHEMA, system=CHECK_SYSTEM, tag=CHECK_USAGE_TAG, allotted=allotted,
            images=[(jpeg, ART_CONTENT_TYPE)])
        verdict = parse_verdict(text)
        if verdict is None:
            raise _Halt.Stop("check_unreadable", "the check's answer is not the verdict's JSON")
        self.checks.append({"pass": verdict.passed, "flags": list(verdict.flags), "notes": verdict.notes})
        return verdict

    async def store(self, store: Callable[..., Awaitable[Any]], jpeg: bytes, width: int, height: int) -> None:
        """(f): only a picture that passed. The store's answer must be this object."""
        allotted = self.slice("store", ())
        sha = hashlib.sha256(jpeg).hexdigest()
        try:
            entry = await asyncio.wait_for(store(jpeg, width=width, height=height), timeout=allotted)
        except asyncio.TimeoutError:
            raise _Halt.Stop("timeout", f"store: not done in {allotted:.0f}s") from None
        except Exception as e:  # noqa: BLE001 — the store is the ledger's; any failure is no art, logged
            raise _Halt.Stop("storage", f"{type(e).__name__}: {str(e)[:160]}") from e
        want = {"sha256": sha, "url": expected_art_url(sha), "bytes": len(jpeg), "width": width, "height": height}
        if not isinstance(entry, Mapping):
            raise _Halt.Stop("storage", "the store kept nothing")
        wrong = sorted(k for k, v in want.items() if entry.get(k) != v)
        if wrong or entry.get("path", art_path(sha)) != art_path(sha):
            logger.error("post art: the store answered another object (%s) run_id=%s — no art",
                         ", ".join(wrong) or "path", self.run_id)
            raise _Halt.Stop("storage", f"the stored entry differs: {', '.join(wrong) or 'path'}")
        self.stored = want

    # ── the record ──

    def usage(self) -> Dict[str, Any]:
        usage: Dict[str, Any] = {
            "images": self.images, "images_uncertain": self.images_uncertain, "image_micros": self.image_micros,
            "image_model": self.image_model or None, "claude": self.claude_usage,
            "claude_uncertain": dict(self.claude_uncertain),
        }
        usage["estimated_micros"] = estimated_micros(usage)
        return usage

    def record(self, reason: Optional[str]) -> Dict[str, Any]:
        ok = reason is None and self.stored is not None
        out: Dict[str, Any] = {
            "version": ART_VERSION,
            "status": STATUS_OK if ok else STATUS_FAILED,
            "reason": None if ok else (reason if reason in ART_REASONS else "internal_error"),
            "company": ({"symbol": self.subject.symbol, "name": self.subject.name}
                        if self.subject is not None else None),
            "surfaces": list(self.subject.surfaces) if self.subject is not None else [],
            "facts": dict(self.facts),
            "subject": self.subject_text,
            "treatment": self.treatment,
            "treatment_source": self.treatment_source,
            "palette": PALETTE,
            "prompt_sha1": self.prompt_sha1,
            "redraw_prompt_sha1": self.redraw_prompt_sha1,
            "briefs": self.briefs,
            "attempts": self.attempts,
            "checks": list(self.checks),
            "models": {"brief": self.served["brief"], "check": self.served["check"],
                       "image": self.image_model if self.attempts else None},
            "usage": self.usage(),
            "seconds": round(max(_mono() - self.started, 0.0), 1),
        }
        if ok:
            out.update(self.stored)
        return out


async def make_post_art(*, run_id: Any, ref: Any, series: Any, output: Any,
                        store: Callable[..., Awaitable[Any]], facts: Optional[Mapping[str, Any]] = None,
                        facts_fn: Optional[Callable[[str], Awaitable[Any]]] = None, record: Any = None,
                        gemini: Any = None, claude: Any = None,
                        budget_seconds: Optional[float] = None) -> Dict[str, Any]:
    """The art record of one accepted template output (module docstring, steps a-f). NEVER raises except
    CancelledError; always returns a JSON-safe record (`status` ok | failed, `reason` ∈ ART_REASONS on a failure).

    `store(jpeg, *, width, height)` keeps a PASSED picture and answers `{path?, url, sha256, bytes, width, height}`
    (run_service.store_art), or None. `facts` — the company's name-free facts ({sector, industry, top_line?}) — or
    `facts_fn(symbol)` to fetch them INSIDE the art budget (best effort, STEP_SECONDS["facts"]). `record`: the news
    record (decision 4's single-company check and its top revenue line). `gemini`: an object with
    `generate_image` (default the shared client). `claude`: the Claude CLIENT the art's `ClaudeOnly` wraps (default
    the shared one). `budget_seconds`: default MARKETING_ART_BUDGET_SECONDS, read now.

    Not gated by MARKETING_ART_ENABLED: the caller decides to make art (the build gates on the switch, the budget
    and the worker's capability; the owner's preview spends with its explicit flag)."""
    run_id, ref, series = str(run_id)[:64], str(ref)[:200], str(series)[:40]
    run = _ArtRun(run_id=run_id, ref=ref, series=series, subject=None, budget=_budget(budget_seconds),
                  gemini=gemini, claude=claude)
    reason: Optional[str] = None
    detail = ""
    try:
        run.subject = art_subject_for(series, output, record)
        if run.subject is None:
            raise _Halt.Stop("not_a_company_series", "the post does not picture one company")
        problem = preflight_problem()
        if problem is not None:
            raise _Halt.Stop(problem, "preflight")
        await run.load_facts(facts, facts_fn)
        await run.brief()
        base = image_prompt(run.subject_text, run.treatment)
        run.prompt_sha1 = prompt_sha1(base)
        jpeg, width, height = await run.draw(base)
        verdict = await run.check(jpeg)
        if not verdict.passed:
            if not run.fits(REDRAW_PATH):
                raise _Halt.Stop("check_failed",
                                 f"the check flagged {list(verdict.flags)} and no time is left to redraw")
            logger.warning("post art: the check flagged %s run_id=%s series=%s — one redraw", list(verdict.flags),
                           run_id, series)
            second = redraw_prompt(base, verdict.flags)
            run.redraw_prompt_sha1 = prompt_sha1(second)
            jpeg, width, height = await run.draw(second)
            verdict = await run.check(jpeg)
            if not verdict.passed:
                raise _Halt.Stop("check_failed", f"the redraw was flagged too: {list(verdict.flags)}")
        await run.store(store, jpeg, width, height)
    except asyncio.CancelledError:
        raise
    except _Halt.Stop as e:
        reason, detail = e.reason, e.detail
    except Exception as e:  # noqa: BLE001 — art never fails a day: a bug here is a record, with its stack
        logger.exception("post art: internal error run_id=%s series=%s (%s: %s)", run_id, series,
                         type(e).__name__, e)
        reason, detail = "internal_error", type(e).__name__
    try:
        out = run.record(reason)
    except Exception as e:  # noqa: BLE001 — the record itself must never raise: a minimal one, unpriceable
        logger.exception("post art: the record could not be built run_id=%s series=%s (%s) — an unpriced failure "
                         "(the monthly cap then reads the month as unreadable)", run_id, series, type(e).__name__)
        out = {"version": ART_VERSION, "status": STATUS_FAILED, "reason": "internal_error",
               "usage": {"images": run.images, "images_uncertain": run.images_uncertain, "estimated_micros": None}}
    try:
        _log_line(out, run_id=run_id, series=series, detail=detail)
    except Exception as e:  # noqa: BLE001 — a log line never fails the art
        logger.exception("post art: the MARKETING_ART line could not be written run_id=%s (%s)", run_id,
                         type(e).__name__)
    return out


def gate_record(reason: Any, *, run_id: Any, ref: Any, series: Any, subject: Optional[ArtSubject] = None,
                detail: str = "") -> Dict[str, Any]:
    """The art record of a run the CALLER's gates stopped BEFORE any call or spend (`script_service._attach_art`:
    not a single-company post, a text-only day, a holder that cannot draw art, the month's budget, an unreadable
    ledger, too little time left) — the very shape of `make_post_art`'s failed record (`status: "failed"`, `reason`
    ∈ ART_REASONS — anything else is `internal_error`), with zero usage (`estimated_micros` 0) and the one
    MARKETING_ART log line. `subject` (when the gate knew it) fills `company` / `surfaces`. Never raises."""
    run_id_s, ref_s, series_s = str(run_id)[:64], str(ref)[:200], str(series)[:40]
    try:
        run = _ArtRun(run_id=run_id_s, ref=ref_s, series=series_s, subject=subject, budget=0.0, gemini=None,
                      claude=None)
        out = run.record(reason if reason in ART_REASONS else "internal_error")
    except Exception as e:  # noqa: BLE001 — a record must always come back: a minimal one, spend 0 (no call made)
        logger.exception("post art: the gate record could not be built run_id=%s series=%s (%s)", run_id_s,
                         series_s, type(e).__name__)
        out = {"version": ART_VERSION, "status": STATUS_FAILED, "reason": "internal_error",
               "usage": {"images": 0, "images_uncertain": 0, "estimated_micros": 0}}
    try:
        _log_line(out, run_id=run_id_s, series=series_s, detail=detail)
    except Exception as e:  # noqa: BLE001 — a log line never fails the art
        logger.exception("post art: the MARKETING_ART line could not be written run_id=%s (%s)", run_id_s,
                         type(e).__name__)
    return out


def _budget(value: Any) -> float:
    """The run's wall budget in seconds: `value`, else MARKETING_ART_BUDGET_SECONDS read now; anything that is not
    a finite non-negative number is 0 (no step fits — a `timeout` record, never a raise)."""
    raw = settings.MARKETING_ART_BUDGET_SECONDS if value is None else value
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw < 0:
        logger.error("post art: the art budget %r is not a finite number of seconds — no step fits", raw)
        return 0.0
    return float(raw)


#: Reasons that are an expected "no art" (the caller's or the post's), logged at INFO; every other failure WARNING.
#: Owner decision 2026-11-20: a loss year's costs-card cover (`costs_cover`) is expected too.
_QUIET_REASONS = frozenset({"not_a_company_series", "costs_cover"})


def _log_line(rec: Mapping[str, Any], *, run_id: str, series: str, detail: str) -> None:
    usage = rec.get("usage") or {}
    company = rec.get("company") or {}
    checks = ",".join("pass" if c.get("pass") else "fail:" + "+".join(c.get("flags") or ())
                      for c in rec.get("checks") or ()) or "-"
    args = (run_id, series, company.get("symbol"), rec.get("status"), rec.get("reason"), rec.get("treatment"),
            rec.get("briefs"), rec.get("attempts"), checks, usage.get("estimated_micros"),
            (rec.get("sha256") or "")[:12] or "-", rec.get("seconds"), _fold(detail, 200) or "-")
    msg = ("MARKETING_ART run_id=%s series=%s symbol=%s status=%s reason=%s treatment=%s briefs=%s draws=%s "
           "checks=%s micros=%s sha=%s in %ss (%s)")
    if rec.get("status") == STATUS_OK or rec.get("reason") in _QUIET_REASONS:
        logger.info(msg, *args)
    elif rec.get("reason") == "internal_error":
        logger.error(msg, *args)
    else:
        logger.warning(msg, *args)


__all__ = [
    "ART_VERSION", "ART_VERSIONS", "ART_PREFIX", "ART_EXT", "ART_CONTENT_TYPE", "ART_MAX_BYTES", "ART_WIDTH",
    "ART_HEIGHT", "ART_MIN_WIDTH", "ART_MAX_WIDTH", "ART_ASPECT", "ART_ASPECT_TOLERANCE", "IMAGE_ASPECT_RATIO",
    "IMAGE_SIZE", "IMAGE_MAX_ATTEMPTS", "ART_DRAWS", "ART_CLAUDE_CALLS", "PALETTE", "MARKETING_TREATMENTS",
    "STEP_SECONDS", "HAPPY_PATH", "REDRAW_PATH", "SURFACE_PICTURE", "SURFACE_COVER", "SURFACES", "PICTURE_SERIES",
    "COVER_ONLY_SERIES", "ART_SERIES", "NEVER_ART_SERIES", "STATUS_OK", "STATUS_FAILED", "ART_REASONS",
    "REASON_WORDS", "CHECK_FLAGS", "NOT_RECOGNISABLE", "FLAG_WORDS", "MARKETING_ADDENDUM", "WORKER_ART_KEYS",
    "ArtSubject", "Verdict", "art_subject_for", "ban_terms", "clean_subject", "subject_problem", "brief_facts",
    "brief_prompt", "image_prompt", "redraw_prompt", "check_prompt", "prompt_sha1", "treatment_words",
    "parse_brief", "parse_verdict", "art_path", "expected_art_url", "art_problem", "drawable_art", "worker_art",
    "claude_call_worst_micros", "run_worst_micros", "estimated_micros", "claude_model_problem", "preflight_problem",
    "normalise_art", "make_post_art", "gate_record", "cover_is_costs_card",
]
