"""The weekly writer's response shapes, and the renderer that turns them into an iOS article.

Two model answers make a piece (plan "Wiser weekly content engine" §1.7):

* the OUTLINE (`Outline`): title, subtitle, card subtitle, tag label, exactly three key
  highlights, 5-7 sections (each with a `kind` in `contracts.SECTION_KINDS`, the fact ids it will
  use and a target length) and 0-3 statistics picked BY FACT ID;
* one SECTION answer per outline section (`SectionDraft`): its content blocks, each a
  `contracts.BLOCK_TYPES` type (no `quote` — weekly pieces carry no quotations).

`render_ios_content` turns them into the EXACT shape of a bundled article in
`frontend/ios/ios/Resources/MoneyMoves/money_moves.json` (keys and nesting, pinned by
`tests/test_learn_weekly_article_schema_parity.py`). Everything a model could fabricate or get
wrong is CODE-OWNED here, never asked of the model:

* the slug (`<kebab-title>-<yyyy-mm-dd>`), the author (`contracts.AUTHOR`, "AI-assisted"), an
  empty `viewCount` (a made-up count is a false claim under the brand), `publishedDaysAgo` 0,
  `isFeatured` (True for a Deep Dive — the featured rule picks the newest), `hasAudioVersion`
  False until narration lands, the read time, the sort order and the category gradient;
* every statistic's VALUE: the model picks a fact id and writes a label; the value is the
  figure exactly as it is written in that fact's text (`fact_figure`), and a statistic whose
  fact has no figure — or whose label names a number or name its fact does not — is dropped,
  never guessed (`render_statistics`);
* related articles, drawn only from BUNDLE catalog articles (a weekly piece can be unpublished,
  and iOS opens a related tile by its title — a tile pointing at a removed piece opens nothing);
* the closing disclosure callout (`append_disclosure`), appended AFTER validation: it names the
  brand, which the validators refuse in model text, and it must never be the model's words.

Section KINDS are a drafting device (the validators allow a dated time word only in a `peg`
section, and cap the peg share): they are kept OUT of the rendered content and returned
separately (`section_kinds`); `draft_view` re-attaches them for `validators.validate_article`.

Pure: Pydantic, stdlib, `contracts`, `schedule` (pure) and the validators' pure helpers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import re
import unicodedata
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, field_validator

from app.services import learn_content_rules as rules
from app.services.learn_weekly import contracts as C
from app.services.learn_weekly import schedule
from app.services.learn_weekly import validators as V
from app.services.marketing import grounding

logger = logging.getLogger(__name__)

# ── shape constants (the prompts state them; the writer checks them) ─────────────────────────

HIGHLIGHT_COUNT = 3
SECTION_COUNT = (5, 7)
MAX_STATISTICS = 3
MAX_BLOCKS_PER_SECTION = 20
STAT_TRENDS: Tuple[str, ...] = ("up", "down", "neutral")
#: One section's target length, in body words, after `normalize_targets`.
SECTION_TARGET_BOUNDS = (80, 600)
#: The peg sections' share of the target words — below `contracts.MAX_PEG_SHARE` (20%), so a peg
#: that runs a little long still passes the article-level peg check.
PEG_TARGET_SHARE = 0.15
#: Words a minute for `readTimeMinutes` (the bundle's reading pace).
READ_WPM = 200
#: Related tiles per article (the bundle shows three in the common case).
RELATED_COUNT = 3
#: Kebab part of a slug: keeps the whole slug far below the ledger's 160-character CHECK.
SLUG_KEBAB_MAX = 80

#: The three category gradients, mirrored from iOS `MoneyMoveCategory.gradientColors`
#: (`LearnModels.swift`; pinned equal by the parity test). A weekly piece has no art of its own
#: until cover art ships (1c), so its hero uses its category's colours.
CATEGORY_GRADIENTS: Mapping[str, Tuple[str, str, str]] = {
    "blueprints": ("059669", "047857", "064E3B"),
    "valueTraps": ("DC2626", "991B1B", "7F1D1D"),
    "battles": ("7C3AED", "5B21B6", "4C1D95"),
}

#: The disclosure callout's code-owned chrome (its text is `contracts.DISCLOSURE_TEXT`).
DISCLOSURE_ICON = "info.circle.fill"
DISCLOSURE_STYLE = "info"

_ICON_ENUM: List[str] = sorted(rules.ICON_ALLOWLIST)
#: The allowed icon names, as the PROMPT lists them (`prompts.SYSTEM_BODY`). They are NOT a schema
#: enum: Gemini compiles a response schema into one constrained-decoding state machine and refuses
#: one that is too big — "400 INVALID_ARGUMENT: The specified schema produces a constraint that has
#: too many states for serving". Measured on gemini-2.5-flash, 2026-10-23: the 79-icon enum alone
#: made BOTH the outline and the section schema fail (every Deep Dive died at its first call);
#: without it both are accepted. An icon is still checked in code (`validators` "icon").
ICON_CHOICES: Tuple[str, ...] = tuple(_ICON_ENUM)
#: The fact-id enum stays (it keeps citations inside the sheet) up to this many ids; the same probe
#: accepted 100 ids and refused 150. A real Deep Dive sheet held 54-63. Above the cap the enum is
#: dropped and the writer's own `unknown_fact` check is the only guard (fail closed either way).
FACT_ID_ENUM_MAX = 80


# ── model answers (Pydantic v2) ──────────────────────────────────────────────────────────────
#
# Types only: a wrong TYPE (a missing key, a list where a string belongs) is an unusable answer
# (the writer's `writer_failed`); counts, kinds, icons, lengths and wording are CONTENT, checked by
# the writer and the validators so the one repair round can name them.


class _Answer(BaseModel):
    # Unknown keys are dropped, never carried into the article: only the fields below are read.
    model_config = ConfigDict(extra="ignore")


class Highlight(_Answer):
    icon: str
    title: str
    description: str


class OutlineSection(_Answer):
    title: str
    icon: str
    kind: str
    fact_ids: List[str] = []
    target_words: int

    @field_validator("target_words", mode="before")
    @classmethod
    def _no_bool(cls, v: Any) -> Any:
        # Pydantic's lax int takes True as 1; a boolean "length" is a malformed answer.
        if isinstance(v, bool):
            raise ValueError("target_words must be a number, not a boolean")
        return v


class OutlineStatistic(_Answer):
    fact_id: str
    label: str
    trend: str = "neutral"


class Outline(_Answer):
    title: str
    subtitle: str
    cardSubtitle: str
    tagLabel: str
    keyHighlights: List[Highlight]
    sections: List[OutlineSection]
    statistics: List[OutlineStatistic] = []


class Block(_Answer):
    type: str
    text: Optional[str] = None
    items: Optional[List[str]] = None
    icon: Optional[str] = None
    style: Optional[str] = None


class SectionDraft(_Answer):
    blocks: List[Block]


def parse_outline(obj: Any) -> Outline:
    """The model's outline object → `Outline`. Raises pydantic `ValidationError` (or TypeError for
    a non-object) on an answer of the wrong SHAPE — the caller's `writer_failed`."""
    if not isinstance(obj, Mapping):
        raise TypeError(f"outline answer is {type(obj).__name__}, not an object")
    return Outline.model_validate(dict(obj))


def parse_section(obj: Any) -> SectionDraft:
    """One section answer → `SectionDraft` (same contract as `parse_outline`)."""
    if not isinstance(obj, Mapping):
        raise TypeError(f"section answer is {type(obj).__name__}, not an object")
    return SectionDraft.model_validate(dict(obj))


# ── Gemini response schemas ──────────────────────────────────────────────────────────────────
#
# A schema is a REQUEST, not a guarantee: every bound below is checked again in code.


def outline_response_schema(fact_ids: Sequence[str] = ()) -> Dict[str, Any]:
    """The outline's schema. `fact_ids` (this sheet's ids) become an enum while there are at most
    `FACT_ID_ENUM_MAX`, so a section or a statistic can only cite a fact that exists — the writer
    still checks (fail closed). Icons are plain strings: see `ICON_CHOICES`."""
    ids = [i for i in dict.fromkeys(fact_ids) if isinstance(i, str) and i]
    fact_id: Dict[str, Any] = {"type": "STRING"}
    if ids and len(ids) <= FACT_ID_ENUM_MAX:
        fact_id["enum"] = ids
    elif ids:
        logger.warning("learn outline schema: %d fact ids exceed FACT_ID_ENUM_MAX=%d — no enum "
                       "(Gemini refuses an over-large schema); the writer's unknown_fact check guards",
                       len(ids), FACT_ID_ENUM_MAX)
    return {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "subtitle": {"type": "STRING"},
            "cardSubtitle": {"type": "STRING"},
            "tagLabel": {"type": "STRING"},
            "keyHighlights": {
                "type": "ARRAY", "minItems": HIGHLIGHT_COUNT, "maxItems": HIGHLIGHT_COUNT,
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "icon": {"type": "STRING"},
                        "title": {"type": "STRING"},
                        "description": {"type": "STRING"},
                    },
                    "required": ["icon", "title", "description"],
                },
            },
            "sections": {
                "type": "ARRAY", "minItems": SECTION_COUNT[0], "maxItems": SECTION_COUNT[1],
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {"type": "STRING"},
                        "icon": {"type": "STRING"},
                        "kind": {"type": "STRING", "enum": list(C.SECTION_KINDS)},
                        "fact_ids": {"type": "ARRAY", "items": dict(fact_id)},
                        "target_words": {"type": "INTEGER"},
                    },
                    "required": ["title", "icon", "kind", "fact_ids", "target_words"],
                },
            },
            "statistics": {
                "type": "ARRAY", "maxItems": MAX_STATISTICS,
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "fact_id": dict(fact_id),
                        "label": {"type": "STRING"},
                        "trend": {"type": "STRING", "enum": list(STAT_TRENDS)},
                    },
                    "required": ["fact_id", "label", "trend"],
                },
            },
        },
        "required": ["title", "subtitle", "cardSubtitle", "tagLabel", "keyHighlights",
                     "sections", "statistics"],
    }


SECTION_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "blocks": {
            "type": "ARRAY", "minItems": 1, "maxItems": MAX_BLOCKS_PER_SECTION,
            "items": {
                "type": "OBJECT",
                "properties": {
                    "type": {"type": "STRING", "enum": list(C.BLOCK_TYPES)},
                    "text": {"type": "STRING"},
                    "items": {"type": "ARRAY", "maxItems": C.MAX_BULLET_ITEMS,
                              "items": {"type": "STRING"}},
                    "icon": {"type": "STRING"},
                    "style": {"type": "STRING", "enum": list(C.CALLOUT_STYLES)},
                },
                # `text` too, on every block (a bulletList's is its lead-in): with only `type`
                # required, constrained decoding looped on bare {"type": "callout"} objects — 2 of
                # 6 live section answers were 15-16 empty blocks and ~130 words; with `text`
                # required, 0 of 12 had an empty block (2026-10-23, gemini-2.5-flash).
                "required": ["type", "text"],
            },
        },
    },
    "required": ["blocks"],
}


# ── code-owned pieces ────────────────────────────────────────────────────────────────────────

_NON_KEBAB = re.compile(r"[^a-z0-9]+")


def make_slug(title: Any, published_date: date) -> str:
    """`<kebab-title>-<yyyy-mm-dd>`: ASCII-folded, lower case, at most `SLUG_KEBAB_MAX` characters
    of title cut at a word boundary. A title with no usable letters gives "money-move" — the date
    still makes it unique to its period. Matches `validators.SLUG_RE` and the ledger's CHECK."""
    if not isinstance(published_date, date):
        raise TypeError(f"published_date must be a date, got {type(published_date).__name__}")
    text = unicodedata.normalize("NFKD", title if isinstance(title, str) else "")
    text = text.encode("ascii", "ignore").decode("ascii").casefold()
    kebab = _NON_KEBAB.sub("-", text).strip("-")
    if len(kebab) > SLUG_KEBAB_MAX:
        cut = kebab[:SLUG_KEBAB_MAX + 1]
        kebab = cut.rsplit("-", 1)[0] if "-" in cut else cut[:SLUG_KEBAB_MAX]
        kebab = kebab.strip("-")
    return f"{kebab or 'money-move'}-{published_date.isoformat()}"


def read_time_minutes(words: int) -> int:
    """`max(1, round(words / READ_WPM))`, rounding half UP (Python's `round` rounds half to even,
    which would read 500 words as 2 minutes and 700 as 4)."""
    if isinstance(words, bool) or not isinstance(words, int) or words < 0:
        words = 0
    return max(1, int(math.floor(words / READ_WPM + 0.5)))


def sort_order_for(kind: str, published_date: date) -> int:
    """The row's `sort_order` (plan §1.10): a Deep Dive `-(iso_year*100 + iso_week)` of its ET
    publish date (always negative, newer = lower), a slot piece `100000 + days since 2026-01-01`."""
    if kind == C.KIND_DEEP_DIVE:
        iso = published_date.isocalendar()
        return schedule.deep_dive_sort_order(iso[0], iso[1])
    if kind == C.KIND_SLOT:
        return schedule.slot_sort_order(published_date)
    raise ValueError(f"unknown piece kind {kind!r}")


# A figure as an SEC sentence writes it: "$130.5 billion", "$84,000", "12.5%", "26,000".
_SCALE = r"(?:\s(?:thousand|million|billion|trillion))"
_FIGURE_RES: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"\$\d{1,3}(?:,\d{3})*(?:\.\d+)?" + _SCALE + r"?(?![\w.,]*\d)"),   # money
    re.compile(r"(?<![\w.$])\d+(?:\.\d+)?(?:%|\spercent\b)"),                      # percent
    re.compile(r"(?<![\w.$,])\d{1,3}(?:,\d{3})*(?:\.\d+)?" + _SCALE),               # scaled count
    re.compile(r"(?<![\w.$,])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?!\w)(?!,\d)"),           # 26,000
)
_FIGURE_SCAN_CAP = 2000


def fact_figure(text: Any) -> Optional[str]:
    """The figure a statistic shows for a fact: the FIRST money amount in its text, else the first
    percentage, else the first scaled count ("1.2 million"), else the first comma-grouped count —
    exactly as written. None when the fact states no such figure (a bare year is not a statistic).
    Pure; reads a capped prefix."""
    if not isinstance(text, str) or not text:
        return None
    head = text[:_FIGURE_SCAN_CAP]
    for rx in _FIGURE_RES:
        m = rx.search(head)
        if m:
            return m.group(0)
    return None


def fact_figures(text: Any) -> List[str]:
    """EVERY distinct figure a fact states (money, percentage, scaled count, comma count), in
    reading order, exactly as written; a match inside a longer one ("1.2 billion" in "$1.2
    billion") is the same figure. Pure; reads the same capped prefix as `fact_figure`."""
    if not isinstance(text, str) or not text:
        return []
    head = text[:_FIGURE_SCAN_CAP]
    spans = sorted(((m.start(), m.end(), m.group(0)) for rx in _FIGURE_RES for m in rx.finditer(head)),
                   key=lambda s: (s[0], s[0] - s[1]))
    out: List[str] = []
    end = -1
    for start, stop, figure in spans:
        if start >= end:
            out.append(figure)
            end = stop
    return list(dict.fromkeys(out))


#: The direction words a fact must state for a card to carry that arrow.
_TREND_WORDS: Dict[str, "re.Pattern[str]"] = {
    "up": re.compile(r"\b(?:increas(?:e|ed|es|ing)|gr[eo]w(?:n|th)?|rose|risen|higher|expanded|"
                     r"doubled|tripled|up\s+from)\b", re.IGNORECASE),
    "down": re.compile(r"\b(?:decreas(?:e|ed|es|ing)|declin(?:e|ed|es|ing)|fell|fallen|lower|"
                       r"reduced|dropped|shrank|down\s+from)\b", re.IGNORECASE),
}


def supported_trend(trend: Any, fact_text: Any) -> str:
    """The arrow a card may show: the model's "up"/"down" only when its fact states that direction
    and not the other; anything else is "neutral". An arrow is a claim — a 2026-10-23 preview put
    "up" on a count of queued projects whose fact gave no change at all."""
    if trend not in ("up", "down") or not isinstance(fact_text, str):
        return "neutral"
    other = "down" if trend == "up" else "up"
    head = fact_text[:_FIGURE_SCAN_CAP]
    if _TREND_WORDS[trend].search(head) and not _TREND_WORDS[other].search(head):
        return trend
    return "neutral"


def render_statistics(statistics: Sequence[OutlineStatistic], sheet: C.FactSheet
                      ) -> List[Dict[str, str]]:
    """The article's `statistics`, values rendered by CODE. A statistic is DROPPED (WARNING,
    never guessed) when its fact is not on the sheet, its fact has no figure, its fact states MORE
    THAN ONE figure, its label fails the grounding of its OWN fact (a number or name the fact does
    not state — "Revenue" on a net-income fact passes, "2024 revenue" on a 2025 fact does not), or
    its label repeats an earlier one.

    One figure only (2026-10-23): the card shows a code-chosen figure under a model-written label,
    and on a fact with several the two came apart — Oracle's $11.6 billion of total cloud revenue
    was shown as "IaaS growth", 8,200 queued projects as "capacity". A single-figure fact cannot
    pair the wrong number with its label. The arrow is `supported_trend`."""
    by_id = sheet.by_id() if isinstance(sheet, C.FactSheet) else {}
    out: List[Dict[str, str]] = []
    seen = set()
    for k, st in enumerate(list(statistics or [])[:MAX_STATISTICS]):
        fact = by_id.get(st.fact_id)
        if fact is None:
            logger.warning("[learn-stat-dropped] statistic %d cites %r, not on the sheet", k,
                           st.fact_id)
            continue
        figures = fact_figures(fact.text)
        if not figures:
            logger.warning("[learn-stat-dropped] statistic %d: fact %s states no figure", k,
                           fact.id)
            continue
        if len(figures) > 1:
            logger.warning("[learn-stat-dropped] statistic %d: fact %s states %d figures (%s) — a "
                           "card could pair the wrong one with its label", k, fact.id, len(figures),
                           ", ".join(figures[:4]))
            continue
        value = figures[0]
        label = (st.label or "").strip()
        key = V.title_key(label)
        if not label or key in seen:
            logger.warning("[learn-stat-dropped] statistic %d: empty or repeated label", k)
            continue
        own = V.build_grounding_context(C.FactSheet(
            kind=sheet.kind, topic_title=sheet.topic_title, theme_slug=sheet.theme_slug,
            subjects=sheet.subjects, facts=(fact,)))
        problems = grounding.check_grounding(f"statistics[{k}].label", label, own)
        if problems:
            logger.warning("[learn-stat-dropped] statistic %d (fact %s): label not grounded in its "
                           "fact (%s)", k, fact.id, ",".join(sorted({p.code for p in problems})))
            continue
        trend = supported_trend(st.trend, fact.text)
        seen.add(key)
        out.append({"value": value, "label": label, "trend": trend})
    return out


def normalize_targets(kinds: Sequence[str], targets: Sequence[int], piece_kind: str
                      ) -> List[int]:
    """Code-owned section lengths: each target clamped to `SECTION_TARGET_BOUNDS`, the peg sections
    together held to `PEG_TARGET_SHARE` of the total, and the whole scaled to the middle of the
    piece's word range (`contracts.DEEP_DIVE_WORDS` / `SLOT_WORDS`). The model proposes the
    proportions; code makes the arithmetic add up, so a length miss never costs a repair call.
    Pure and deterministic."""
    bounds = C.DEEP_DIVE_WORDS if piece_kind == C.KIND_DEEP_DIVE else C.SLOT_WORDS
    goal = (bounds[0] + bounds[1]) // 2
    lo, hi = SECTION_TARGET_BOUNDS
    raw = []
    for t in targets:
        v = t if isinstance(t, int) and not isinstance(t, bool) else lo
        raw.append(min(max(v, lo), hi))
    n = len(raw)
    if n == 0:
        return []
    peg = [i for i, k in enumerate(kinds) if k == "peg"]
    rest = [i for i in range(n) if i not in peg]
    peg_goal = int(goal * PEG_TARGET_SHARE)
    out = [0] * n
    if peg and rest:
        peg_sum = sum(raw[i] for i in peg)
        peg_budget = min(peg_sum * goal / max(sum(raw), 1), peg_goal)
        for i in peg:
            out[i] = raw[i] * peg_budget / max(peg_sum, 1)
        rest_sum = sum(raw[i] for i in rest)
        for i in rest:
            out[i] = raw[i] * (goal - peg_budget) / max(rest_sum, 1)
    else:
        total = sum(raw)
        out = [r * goal / max(total, 1) for r in raw]
    # Integer words; a peg section may sit below the per-section floor (a short dated paragraph).
    return [max(int(round(v)), 40) for v in out]


def normalize_outline(outline: Outline, piece_kind: str) -> Outline:
    """A copy of `outline` with the code-owned parts applied: text trimmed, the tag label upper
    case, each section's fact ids de-duplicated (order kept), its `target_words` replaced by
    `normalize_targets`, at most `MAX_STATISTICS` statistics, and an unreadable trend shown as
    "neutral". Counts, kinds and icons are NOT fixed here — those are content the repair names.
    Deterministic and (up to integer rounding) idempotent, so a resumed outline renders the same."""
    kinds = [s.kind for s in outline.sections]
    targets = normalize_targets(kinds, [s.target_words for s in outline.sections], piece_kind)
    sections = []
    for sec, target in zip(outline.sections, targets):
        ids = list(dict.fromkeys(f.strip() for f in sec.fact_ids if isinstance(f, str) and f.strip()))
        sections.append(sec.model_copy(update={"title": sec.title.strip(), "fact_ids": ids,
                                               "target_words": target}))
    stats = [st.model_copy(update={"fact_id": st.fact_id.strip(), "label": st.label.strip(),
                                   "trend": st.trend if st.trend in STAT_TRENDS else "neutral"})
             for st in outline.statistics[:MAX_STATISTICS]]
    return outline.model_copy(update={
        "title": outline.title.strip(), "subtitle": outline.subtitle.strip(),
        "cardSubtitle": outline.cardSubtitle.strip(), "tagLabel": outline.tagLabel.strip().upper(),
        "sections": sections, "statistics": stats,
    })


def section_kinds(outline: Outline) -> List[str]:
    """The DRAFT kinds of the outline's sections, in order (kept out of the rendered content)."""
    return [s.kind for s in outline.sections]


#: The icon a callout gets when the model leaves its icon out or names one off the allow-list:
#: one per style (a callout with no valid style is an `info` callout). Icons are decoration —
#: nothing a reader is told depends on one — so this is a default, never a rewrite of content.
CALLOUT_STYLE_ICONS: Dict[str, str] = {
    "info": DISCLOSURE_ICON, "warning": "exclamationmark.triangle.fill", "highlight": "lightbulb.fill",
}


def section_blocks(draft: SectionDraft, title: Optional[str] = None) -> List[Dict[str, Any]]:
    """One section answer → its iOS content blocks, each carrying ONLY the keys its type uses
    (`paragraph`/`subheading`: text; `callout`: icon, style, text; `bulletList`: items). Text is
    trimmed; no reader-visible TEXT is rewritten — a bad block reaches the validators as it is, so
    the refusal names it. An unknown type keeps its text so `validate_block` can refuse it by type.

    The exceptions, all measured on gemini-2.5-flash (2026-10-23 previews):
      * An EMPTY block (no text and no items) is dropped with a WARNING. Constrained decoding with
        only `type` required can loop on the smallest valid object — ten bare {"type": "callout"}
        in a row sank whole sections, and a block with nothing in it has nothing to judge.
      * A `bulletList`'s text is its lead-in ("Acquisitions can help a company:"): it becomes a
        `paragraph` just before the list — the items are fragments without it — unless it repeats
        another block of the answer (the model's own paragraph, written twice).
      * A callout's missing / off-list icon and missing / unknown style get the defaults in
        `CALLOUT_STYLE_ICONS` (decoration only; the list is in the prompt, no longer a schema enum).
      * A `subheading` that repeats the section's own `title` (same `validators.title_key`) is
        dropped: iOS already draws the title above the blocks, so readers saw every heading twice
        (five of six sections in one preview).
    """
    out: List[Dict[str, Any]] = []
    empty = repeated = 0
    title_key = V.title_key(title) if isinstance(title, str) and title.strip() else None
    # A lead-in that repeats ANY other block of this answer (before or after it) is dropped: the
    # article's duplicate rule has no repair (review round 3).
    other_keys = {V.block_key(b.text) for b in draft.blocks
                  if b.type != "bulletList" and isinstance(b.text, str) and b.text.strip()}
    lead_keys: set = set()
    for b in draft.blocks:
        text = b.text.strip() if isinstance(b.text, str) else b.text
        items = [i.strip() for i in (b.items or []) if isinstance(i, str) and i.strip()]
        if not text and not items:
            empty += 1
            continue
        if (b.type == "subheading" and title_key is not None and isinstance(text, str)
                and V.title_key(text) == title_key):
            repeated += 1
            continue
        if b.type == "bulletList":
            if items and isinstance(text, str) and text:
                key = V.block_key(text)
                if key and (key in other_keys or key in lead_keys):
                    repeated += 1          # the model's own paragraph, repeated as the lead-in
                else:
                    lead_keys.add(key)
                    out.append({"type": "paragraph", "text": text})
            out.append({"type": "bulletList", "items": [i.strip() for i in (b.items or [])]})
        elif b.type == "callout":
            style = b.style if b.style in CALLOUT_STYLE_ICONS else "info"
            icon = b.icon if isinstance(b.icon, str) and b.icon in rules.ICON_ALLOWLIST \
                else CALLOUT_STYLE_ICONS[style]
            out.append({"type": "callout", "icon": icon, "style": style, "text": text})
        else:
            out.append({"type": b.type, "text": text})
    if empty:
        logger.warning("learn section: dropped %d empty block(s) of %d (no text, no items)",
                       empty, len(draft.blocks))
    if repeated:
        logger.info("learn section: dropped %d repeated block(s) (a subheading repeating the "
                    "section title %r, or a list lead-in repeating a block)", repeated, title)
    return out


def body_words(blocks: Sequence[Mapping[str, Any]]) -> int:
    """Body words of some blocks, counted exactly as `validators.validate_article` counts them."""
    words = 0
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        words += V.word_count(block.get("text"))
        items = block.get("items")
        if isinstance(items, list):
            words += sum(V.word_count(i) for i in items)
    return words


def related_articles(catalog: Sequence[Mapping[str, Any]], category: str, *,
                     exclude_titles: Sequence[str] = (), limit: int = RELATED_COUNT
                     ) -> List[Dict[str, Any]]:
    """Up to `limit` related tiles from the served catalog: same category first, then the others,
    each group in catalog order. Only BUNDLE articles qualify (a weekly piece — slug ending in a
    date, or `source` weekly — can be unpublished, leaving a tile that opens nothing), and only
    rows carrying everything the tile shows (title, subtitle, a known category, a whole read time).
    Tile shape = the bundle's: title, subtitle, category, readTimeMinutes, viewCount "", and the
    first two of the article's hero colours (its category's when it has none)."""
    from app.services.marketing.content_pool import weekly_piece_reason  # pure, FMP-free

    excluded = {V.title_key(t) for t in exclude_titles if isinstance(t, str)}
    same: List[Dict[str, Any]] = []
    other: List[Dict[str, Any]] = []
    seen = set()
    for art in catalog or ():
        if not isinstance(art, Mapping) or weekly_piece_reason(dict(art)) is not None:
            continue
        title, subtitle, cat = art.get("title"), art.get("subtitle"), art.get("category")
        minutes = art.get("readTimeMinutes")
        if not (isinstance(title, str) and title.strip() and isinstance(subtitle, str)
                and subtitle.strip() and cat in C.CATEGORIES
                and isinstance(minutes, int) and not isinstance(minutes, bool) and minutes >= 1):
            continue
        key = V.title_key(title)
        if not key or key in excluded or key in seen:
            continue
        seen.add(key)
        hero = art.get("heroGradientColors")
        colors = ([c for c in hero[:2] if isinstance(c, str)]
                  if isinstance(hero, list) else [])
        if len(colors) != 2:
            colors = list(CATEGORY_GRADIENTS[cat][:2])
        tile = {"title": title, "subtitle": subtitle, "category": cat,
                "readTimeMinutes": minutes, "viewCount": "", "gradientColors": colors}
        (same if cat == category else other).append(tile)
    return (same + other)[:max(0, int(limit))]


# ── the renderer ─────────────────────────────────────────────────────────────────────────────


def render_ios_content(outline: Outline, sections: Sequence[Sequence[Mapping[str, Any]]], *,
                       kind: str, category: str, slug: str, published_date: date,
                       sheet: C.FactSheet, related_titles: Sequence[Mapping[str, Any]] = ()
                       ) -> Dict[str, Any]:
    """The iOS article (`MoneyMoveArticleDTO` shape) for `outline` + its sections' blocks
    (`section_blocks`, one list per outline section, same order). `related_titles` are served
    catalog ARTICLES (dicts) the related tiles are drawn from (`related_articles`). Section kinds
    are NOT in the result (`section_kinds`); the disclosure is NOT in it (`append_disclosure`).
    Raises ValueError for a caller bug (unknown kind/category, sections not parallel to the
    outline); everything the model wrote is passed through for the validators to judge."""
    if kind not in C.KINDS:
        raise ValueError(f"unknown piece kind {kind!r}")
    if category not in C.CATEGORIES:
        raise ValueError(f"category {category!r} not in {C.CATEGORIES}")
    if len(sections) != len(outline.sections):
        raise ValueError(f"{len(sections)} section block lists for {len(outline.sections)} "
                         "outline sections")
    rendered_sections: List[Dict[str, Any]] = []
    words = 0
    for i, (sec, blocks) in enumerate(zip(outline.sections, sections)):
        content = [dict(b) for b in blocks]
        words += body_words(content)
        entry: Dict[str, Any] = {"title": sec.title.strip(), "icon": sec.icon}
        if i == 0:
            entry["hasGlowEffect"] = True       # the bundle's first section glows
        entry["content"] = content
        rendered_sections.append(entry)
    title = outline.title.strip()
    return {
        "slug": slug,
        "title": title,
        "subtitle": outline.subtitle.strip(),
        "cardSubtitle": outline.cardSubtitle.strip(),
        "category": category,
        "author": dict(C.AUTHOR),
        "readTimeMinutes": read_time_minutes(words),
        "sortOrder": sort_order_for(kind, published_date),
        "publishedDaysAgo": 0,
        "tagLabel": outline.tagLabel.strip().upper(),
        "isFeatured": kind == C.KIND_DEEP_DIVE,
        "hasAudioVersion": False,
        "audioUrl": None,
        "viewCount": "",
        "heroGradientColors": list(CATEGORY_GRADIENTS[category]),
        "keyHighlights": [{"icon": h.icon, "title": h.title.strip(),
                           "description": h.description.strip()}
                          for h in outline.keyHighlights],
        "sections": rendered_sections,
        "statistics": render_statistics(outline.statistics, sheet),
        "relatedArticles": related_articles(related_titles, category, exclude_titles=[title]),
    }


def draft_view(content: Mapping[str, Any], kinds: Sequence[str]) -> Dict[str, Any]:
    """A deep copy of `content` with each section's draft `kind` re-attached — the shape
    `validators.validate_article` reads. A kinds list of the wrong length leaves the extra
    sections without a kind (the validator refuses them: fail closed)."""
    view = copy.deepcopy(dict(content))
    secs = view.get("sections")
    if isinstance(secs, list):
        for sec, k in zip(secs, kinds):
            if isinstance(sec, dict):
                sec["kind"] = k
    return view


def disclosure_block() -> Dict[str, str]:
    return {"type": "callout", "icon": DISCLOSURE_ICON, "style": DISCLOSURE_STYLE,
            "text": C.DISCLOSURE_TEXT}


def append_disclosure(content: Mapping[str, Any]) -> Dict[str, Any]:
    """A deep copy of `content` with the closing disclosure callout appended to the LAST section —
    after validation, never validated (it names the brand on purpose). Idempotent: a last block
    that already is the disclosure is not appended twice. ValueError when there is no section to
    carry it (a caller bug: such an article never passes validation)."""
    out = copy.deepcopy(dict(content))
    secs = out.get("sections")
    if not isinstance(secs, list) or not secs or not isinstance(secs[-1], dict):
        raise ValueError("append_disclosure: the article has no section to carry the disclosure")
    blocks = secs[-1].get("content")
    if not isinstance(blocks, list):
        raise ValueError("append_disclosure: the last section has no content list")
    if not (blocks and blocks[-1] == disclosure_block()):
        blocks.append(disclosure_block())
    return out


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def content_sha256(content: Any) -> str:
    """`draft_sha256` (contract 5): sha256 of the canonical JSON of the stored draft. It fences the
    review buttons, so it must change whenever the draft does (narration timings included) — and
    canonical JSON (sorted keys, no whitespace) makes it independent of dict order."""
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


__all__ = [
    "Block", "CATEGORY_GRADIENTS", "DISCLOSURE_ICON", "DISCLOSURE_STYLE", "HIGHLIGHT_COUNT",
    "MAX_BLOCKS_PER_SECTION", "MAX_STATISTICS", "Outline", "OutlineSection", "OutlineStatistic",
    "SECTION_COUNT", "SECTION_RESPONSE_SCHEMA", "STAT_TRENDS", "SectionDraft", "append_disclosure",
    "body_words", "canonical_json", "content_sha256", "disclosure_block", "draft_view",
    "fact_figure", "make_slug", "normalize_outline", "normalize_targets", "outline_response_schema",
    "parse_outline",
    "parse_section", "read_time_minutes", "related_articles", "render_ios_content",
    "render_statistics", "section_blocks", "section_kinds", "sort_order_for",
]
