"""
The class-A content pool: Learn corpus → eligible items → fact sheets (SYSTEM_DESIGN_GUIDELINES §12.5).

Source of truth is the two BUNDLED Learn documents in `backend/data/` (`money_moves.json`,
`journey_lessons.json` — byte-identical to the iOS bundle, pinned by the Learn parity tests, and
shipped in the web image). Reading the bundle rather than Supabase keeps selection pure and
deterministic: the same corpus always yields the same pool, on any host, with no I/O at request
time beyond one cached file read (precedent: `chat_starters_service._load_bundled_catalogue`).

What becomes a FACT SHEET — the only text the writer sees and the only facts a post may use:

1. Flatten the item (titles, highlights, statistics, section blocks, Journey cards) and drop the
   noise a writer must never see twice or at all: read-along timing arrays, `**bold**` markup,
   icon names, and EVERY quote block with its attribution (a quote is a real person's words).
2. Split into sentences and drop every sentence the OUTPUT compliance scan would reject — names a
   real person, states a share price / valuation / market cap, calls a company a re-rating or
   an investment, rewards its owners, carries a % return (spelled out too), a link, a vendor
   name. Prompt, fact sheet and validator therefore agree: the writer cannot ground a
   claim the validator would refuse, because the claim is not in front of it. In a Money Moves
   item that includes every sentence pointing at a person by ROLE or PRONOUN ("what its own CEO
   called 'production hell'", "What he actually bought."): once the sentence naming someone is
   dropped, its neighbours still describe them, and a writer told to use the sheet would copy
   them. Journey is exempt (strict mode off): its "he" is the fictional Mr. Market.
3. An item is ELIGIBLE only if it is not hand-excluded (`EXCLUDED`, each with its reason) and at
   least `MIN_FACT_WORDS` survive cleaning.

Pure and FMP-free (imports only the marketing compliance/grounding/numbers modules).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from app.services.marketing.compliance import (
    clean,
    scan_text,
    sentence_company_mentions,
    sentences,
    skeleton,
)
from app.services.marketing.grounding import (
    GroundingContext,
    build_context,
    full_vocab,
    ordinary,
    vocab_roots,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
MONEY_MOVES_PATH = DATA_DIR / "money_moves.json"
JOURNEY_PATH = DATA_DIR / "journey_lessons.json"

MONEY_MOVES = "money_moves"
JOURNEY = "journey"

#: A fact sheet thinner than this cannot carry a 150-word script without padding (which is where
#: invention starts), so the item sits out.
MIN_FACT_WORDS = 150

#: Hand-curated exclusions, each with its reason. Keys must exist in the corpus
#: (`test_marketing_content_pool.py` fails on a stale key).
EXCLUDED: Dict[str, str] = {
    # Built around a real investor: the App Store "Do not use" list and the migration-103
    # impersonation boundary apply to marketing copy.
    "money_moves:warren-buffetts-early-days": "biography of a named real investor",
    "journey:buffett_way": "method lesson built on a named real investor",
    "journey:lynch_way": "method lesson built on a named real investor",
    "journey:cathie_wood_way": "method lesson built on a named real investor",
    "journey:inversion": "built around a real investor's quotation",
    # Names the model vendor; the identity scan would reject most of it.
    "money_moves:google-vs-microsoft-ai-wars": "names the underlying model vendor's product",
    # Unsourced, time-relative figures ("340% increase from last year", "Industry Analyst").
    "money_moves:the-future-of-digital-finance": "unsourced time-relative statistics",
    # UK cryptoasset financial-promotion rules; a download CTA beside crypto is the risky shape.
    "journey:bitcoin_digital_gold": "cryptoasset promotion risk (UK)",
    "journey:tokenomics": "cryptoasset promotion risk (UK)",
    # Promotes the 13F feature, which is FMP-relayed data, with copy-the-investors framing.
    "journey:whale_watching": "promotes an FMP-relayed feature (13F tracking)",
    # Misconduct stories centred on identifiable people. With names removed the people are
    # still identifiable by role ("the founder", "the auditors"), and an LLM retelling adds
    # claims the source never made — the defamation shape the plan rules out.
    "money_moves:the-fall-of-enron": "misconduct story centred on identifiable people",
    "money_moves:the-ftx-collapse": "misconduct story centred on identifiable people",
    "money_moves:theranos-blood-and-lies": "misconduct story centred on identifiable people",
    "money_moves:weworks-unraveling": "founder-conduct story centred on an identifiable person",
    # The article's lesson IS whether a named company's stock was cheap (a value trap). The
    # user's rule for Money Moves (2026-09-23): companies as case studies, never their value.
    "money_moves:the-fall-of-sears": "the lesson is a valuation verdict on a named company",
}

_BOLD_RE = re.compile(r"\*\*|__")
_SENTENCE_END_RE = re.compile(r"[.!?]$")


def strict_instruments(kind: str) -> bool:
    """Money Moves posts are about named companies, so the neutral valuation vocabulary is
    rejected there; Journey posts name no instrument (see `compliance.CLASS_B_TIER1`)."""
    return kind != JOURNEY


@dataclass(frozen=True)
class ContentItem:
    key: str
    kind: str
    slug: str
    title: str
    category: str
    fact_sentences: Tuple[str, ...]
    dropped: Tuple[Tuple[str, str], ...]     # (sentence, first violation code)
    word_count: int
    grounding: GroundingContext
    excluded_reason: Optional[str]
    company_terms: FrozenSet[str] = frozenset()

    @property
    def fact_text(self) -> str:
        return "\n".join(self.fact_sentences)

    @property
    def eligible(self) -> bool:
        return self.excluded_reason is None and self.word_count >= MIN_FACT_WORDS

    @property
    def ineligible_reason(self) -> Optional[str]:
        if self.excluded_reason:
            return self.excluded_reason
        if self.word_count < MIN_FACT_WORDS:
            return f"fact sheet too thin after cleaning ({self.word_count} words)"
        return None


# ── flattening ────────────────────────────────────────────────────────────────


def _as_sentence(text: str) -> str:
    t = _BOLD_RE.sub("", clean(text))
    t = re.sub(r"\s+", " ", t).strip()
    if t and not _SENTENCE_END_RE.search(t):
        t += "."
    return t


def _money_moves_blocks(article: Dict[str, Any]) -> List[str]:
    blocks: List[str] = []
    for key in ("title", "subtitle", "cardSubtitle"):
        if article.get(key):
            blocks.append(str(article[key]))
    for h in article.get("keyHighlights") or []:
        if isinstance(h, dict) and (h.get("title") or h.get("description")):
            blocks.append(f"{h.get('title') or ''}: {h.get('description') or ''}".strip(": "))
    for s in article.get("statistics") or []:
        if isinstance(s, dict) and s.get("value") and s.get("label"):
            blocks.append(f"{s['label']}: {s['value']}")
    for section in article.get("sections") or []:
        if not isinstance(section, dict):
            continue
        if section.get("title"):
            blocks.append(str(section["title"]))
        for block in section.get("content") or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "quote":
                continue  # a real person's words + attribution: never fact-sheet material
            if kind == "bulletList":
                blocks.extend(str(i) for i in (block.get("items") or []) if i)
            elif block.get("text"):
                blocks.append(str(block["text"]))
    return blocks


def _journey_blocks(lesson: Dict[str, Any]) -> List[str]:
    blocks: List[str] = []
    for key in ("title", "description"):
        if lesson.get(key):
            blocks.append(str(lesson[key]))
    for card in lesson.get("cards") or []:
        if not isinstance(card, dict):
            continue
        if card.get("headline"):
            blocks.append(str(card["headline"]))
        if card.get("text"):
            blocks.append(str(card["text"]))
    return blocks


def _split(blocks: List[str]) -> List[str]:
    out: List[str] = []
    for b in blocks:
        for s in sentences(_BOLD_RE.sub("", clean(b))):
            s = _as_sentence(s)
            if s:
                out.append(s)
    return out


# ── corpus ────────────────────────────────────────────────────────────────────


def _read(path: Path, top_key: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # Loud: an unreadable corpus means an empty pool, and every day becomes a skip.
        logger.error("marketing content pool: cannot read %s (%s: %s)", path.name,
                     type(e).__name__, e)
        return []
    # A document that parses but has the wrong shape contributes nothing, and when the OTHER
    # file is fine nothing downstream notices half the pool is gone — so say which and why.
    if not isinstance(data, dict):
        logger.error("marketing content pool: %s has the wrong shape (top level is %s, expected "
                     "an object with a %r list); contributing no items", path.name,
                     type(data).__name__, top_key)
        return []
    if top_key not in data:
        logger.error("marketing content pool: %s has the wrong shape (no %r key; keys: %s); "
                     "contributing no items", path.name, top_key, sorted(map(str, data))[:10])
        return []
    rows = data[top_key]
    if not isinstance(rows, list):
        logger.error("marketing content pool: %s has the wrong shape (%r is %s, expected a list); "
                     "contributing no items", path.name, top_key, type(rows).__name__)
        return []
    kept = [r for r in rows if isinstance(r, dict) and r.get("slug")]
    if len(kept) != len(rows):
        logger.warning("marketing content pool: %s skipped %d of %d %r rows (not an object, or no "
                       "slug)", path.name, len(rows) - len(kept), len(rows), top_key)
    return kept


def _raw_items() -> List[Tuple[str, str, str, str, str, List[str]]]:
    """(key, kind, slug, title, category, sentences) for every corpus item, eligible or not."""
    out = []
    for a in _read(MONEY_MOVES_PATH, "articles"):
        out.append((f"{MONEY_MOVES}:{a['slug']}", MONEY_MOVES, str(a["slug"]),
                    str(a.get("title") or ""), str(a.get("category") or ""),
                    _split(_money_moves_blocks(a))))
    for lesson in _read(JOURNEY_PATH, "lessons"):
        out.append((f"{JOURNEY}:{lesson['slug']}", JOURNEY, str(lesson["slug"]),
                    str(lesson.get("title") or ""), str(lesson.get("level") or ""),
                    _split(_journey_blocks(lesson))))
    return out


def _vocab(all_sentences: List[str]) -> FrozenSet[str]:
    """Words that occur in LOWER case somewhere in the corpus — ordinary vocabulary. A proper
    noun only ever appears capitalised, so it is absent here and must be grounded per item."""
    words = set()
    for s in all_sentences:
        for w in re.findall(r"(?<![A-Za-z])[a-z][a-z'-]*", s):
            words.add(w.strip("'-"))
    return frozenset(w for w in words if w)


_CAP_TOKEN_RE = re.compile(r"(?<![A-Za-z])[A-Z][A-Za-z0-9&'-]*")
#: Never a company, however a heading capitalises it ("in March 2019", "Friday").
_CALENDAR_WORDS = frozenset("""
january february march april may june july august september october november december
monday tuesday wednesday thursday friday saturday sunday
""".split())
#: A "Label: value" row (a statistic or a highlight): the label is a HEADING.
_LABEL_RE = re.compile(r"^[^:]{1,80}:\s")


def _lower_forms(sentence: str) -> List[str]:
    """Every lower-case word as written, plus its hyphen parts ("high-margin" → "margin") and
    its singular ("margins" → "margin"): proof the item uses the word as an ordinary word."""
    out: List[str] = []
    for w in re.findall(r"(?<![A-Za-z])[a-z][a-z'-]*", sentence):
        for part in [w] + [p for p in w.split("-") if p]:
            out.append(part)
            if len(part) > 3 and part.endswith("s") and not part.endswith("ss"):
                out.append(part[:-1])
    return out


def proper_nouns(sents: List[str], vocab: FrozenSet[str], roots: FrozenSet[str]) -> FrozenSet[str]:
    """Lower-case names in an item (Costco, Kirkland, LVMH, Sears…), used to make a tier-2
    evaluative word a violation next to an issuer. A token is a name if it is not an ordinary
    word, OR if this item writes it capitalised mid-sentence at least twice and never in lower
    case — which is what catches the names that ARE words ("Sears", "Apple", "Visa").

    content-B review (idx 47): the mid-sentence count skips the LABEL half of a "Label: value"
    row (a heading — "Reels Launches: 2020"), month and day names ("March"), and a word the item
    writes in lower case inside a compound or as a plural ("high-margin", "margins"), so heading
    words stop reading as issuers. Every company the lexicon finds in the item is a term too
    (`compliance.sentence_company_mentions`), so "Meta", "Home Depot", "Apple" and "Visa" can
    never fall out with a heading word."""
    out = set()
    mid_caps: Dict[str, int] = {}
    lower_seen = set()
    for s in sents:
        lower_seen.update(_lower_forms(s))
        label = _LABEL_RE.match(s)
        label_end = label.end() if label else 0
        for m in _CAP_TOKEN_RE.finditer(s):
            low = m.group(0).lower().strip("'-")
            if low.endswith("'s"):
                low = low[:-2]
            if len(low) < 2:
                continue
            if m.start() > 0 and m.start() >= label_end and low not in _CALENDAR_WORDS:
                mid_caps[low] = mid_caps.get(low, 0) + 1
            parts = [p for p in low.split("-") if p]
            if not all(ordinary(p, vocab, roots) for p in parts):
                out.add(low)
        # Single-word names only: a term is matched as ONE token, and the last word of a
        # multi-word name ("Platforms", "Instruments") is no issuer on its own.
        out.update(name for name, _a, _b in sentence_company_mentions(skeleton(s))
                   if " " not in name)
    out.update(w for w, n in mid_caps.items() if n >= 2 and w not in lower_seen)
    return frozenset(out)


@lru_cache(maxsize=1)
def load_corpus() -> Dict[str, ContentItem]:
    """Every corpus item, keyed `<kind>:<slug>`, with its cleaned fact sheet. Cached for the
    process: the bundle only changes with a deploy."""
    raw = _raw_items()
    vocab = _vocab([s for *_rest, sents in raw for s in sents])
    base = full_vocab(vocab)
    roots = vocab_roots(base)
    items: Dict[str, ContentItem] = {}
    for key, kind, slug, title, category, sents in raw:
        companies = proper_nouns(sents, base, roots) if strict_instruments(kind) else frozenset()
        kept: List[str] = []
        dropped: List[Tuple[str, str]] = []
        for s in sents:
            violations = scan_text("source", s, allow_emoji=True,
                                   strict_instruments=strict_instruments(kind),
                                   company_terms=companies)
            if violations:
                dropped.append((s, violations[0].code))
            else:
                kept.append(s)
        words = sum(len(s.split()) for s in kept)
        items[key] = ContentItem(
            key=key, kind=kind, slug=slug, title=title, category=category,
            fact_sentences=tuple(kept), dropped=tuple(dropped), word_count=words,
            grounding=build_context(kept, vocab),
            excluded_reason=EXCLUDED.get(key),
            company_terms=companies,
        )
    if not items:
        logger.error("marketing content pool: the Learn corpus produced NO items")
    return items


def eligible_keys() -> List[str]:
    return sorted(k for k, item in load_corpus().items() if item.eligible)


def get_item(key: str) -> Optional[ContentItem]:
    return load_corpus().get(key)
