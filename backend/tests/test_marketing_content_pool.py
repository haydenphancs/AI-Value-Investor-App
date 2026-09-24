"""
`app/services/marketing/content_pool.py` — the class-A pool: Learn corpus → eligible items →
cleaned fact sheets (SYSTEM_DESIGN_GUIDELINES §12.5, `.claude/rules/marketing.md` §1).

Written independently of the module, adversarially. What is pinned, and why each matters:

* **The corpus-wide invariant.** Every fact sentence of every ELIGIBLE item already passes the
  output scan (compliance + grounding) with that item's own `strict_instruments(kind)` and
  `company_terms`. Prompt, fact sheet and validator therefore agree: the writer is never shown a
  sentence the validator would reject if copied verbatim.
* **Nothing a post must never carry reaches a fact sheet** — quote blocks and their attributions
  (a real person's words), read-along timing arrays, icon names, `**bold**` markup, CTA ids.
  Proved twice: over the real bundle, and on a synthetic article/lesson whose unwanted fields
  carry unique sentinels (the real read-along text duplicates the paragraph, so only a sentinel
  can show it was skipped rather than merely identical).
* **`EXCLUDED` cannot rot** — every key exists, every reason is real — and the investor rule is
  DERIVED from the raw corpus rather than read back from `EXCLUDED`, so a new biography article
  cannot slip in un-excluded.
* **A floor on the eligible pool**, so the corpus-wide guards cannot go vacuous by the pool
  shrinking to nothing.
* **An unreadable corpus degrades to an empty pool with an ERROR log**, never an exception.

Tests that swap the corpus paths clear `load_corpus`'s cache before AND after (fixture), so the
real corpus is re-read by whichever test runs next.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest

from app.services.marketing import compliance
from app.services.marketing import content_pool as cp
from app.services.marketing.grounding import check_grounding, full_vocab, ordinary, vocab_roots

_LOGGER = "app.services.marketing.content_pool"

#: Leaves that are not prose: read-along arrays duplicate the text they time, and the rest are
#: identifiers / file names / colours. Skipped when deriving "which people does an item name".
_NON_PROSE_KEYS = frozenset({
    "readAlong", "itemsReadAlong", "readAlongWords", "slug", "audioClip", "audioUrl", "icon",
    "heroGradientColors", "cta",
})


# ── helpers ───────────────────────────────────────────────────────────────────


def _raw_docs() -> Dict[str, Dict[str, Any]]:
    """The two bundled documents, keyed exactly as the pool keys them — read here from disk,
    independently of the module's own reader."""
    mm = json.loads(cp.MONEY_MOVES_PATH.read_text(encoding="utf-8"))["articles"]
    jr = json.loads(cp.JOURNEY_PATH.read_text(encoding="utf-8"))["lessons"]
    out = {f"{cp.MONEY_MOVES}:{a['slug']}": a for a in mm}
    out.update({f"{cp.JOURNEY}:{lesson['slug']}": lesson for lesson in jr})
    return out


def _prose_leaves(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for x in obj:
            yield from _prose_leaves(x)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in _NON_PROSE_KEYS:
                yield from _prose_leaves(v)


def _values_for_key(obj: Any, key: str) -> Iterator[Any]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from _values_for_key(v, key)
    elif isinstance(obj, list):
        for x in obj:
            yield from _values_for_key(x, key)


def _quote_blocks(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for section in doc.get("sections") or []:
        for block in section.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "quote":
                out.append(block)
    return out


def _eligible_items() -> List[cp.ContentItem]:
    corpus = cp.load_corpus()
    return [corpus[k] for k in cp.eligible_keys()]


@pytest.fixture
def swap_corpus(monkeypatch):
    """Point the pool at other files; the cache is cleared on the way in and on the way out."""
    cp.load_corpus.cache_clear()

    def _swap(money_moves: Path, journey: Path) -> None:
        monkeypatch.setattr(cp, "MONEY_MOVES_PATH", money_moves)
        monkeypatch.setattr(cp, "JOURNEY_PATH", journey)
        cp.load_corpus.cache_clear()

    try:
        yield _swap
    finally:
        # Runs before monkeypatch restores the paths, which is fine: an EMPTY cache is all the
        # next caller needs — it re-reads from whatever the constants are by then (the real ones).
        cp.load_corpus.cache_clear()


def _write(path: Path, payload: Any) -> Path:
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


# ── EXCLUDED and the pool floor ───────────────────────────────────────────────


def test_every_excluded_key_exists_in_the_corpus_and_has_a_reason():
    raw = _raw_docs()
    corpus = cp.load_corpus()
    stale = sorted(k for k in cp.EXCLUDED if k not in raw)
    assert not stale, (
        f"EXCLUDED names items that no longer exist in the bundle: {stale}. If a slug was "
        f"RENAMED, rename the key — do not delete the line: deleting it re-admits the item "
        f"under its new slug (POLICY_EXCLUSIONS below pins every policy key)."
    )
    for key, reason in cp.EXCLUDED.items():
        assert isinstance(reason, str) and len(reason.strip()) >= 10, (key, reason)
        item = corpus[key]
        assert item.excluded_reason == reason
        assert not item.eligible and item.ineligible_reason == reason
        assert key not in cp.eligible_keys()


def test_every_bundled_item_is_in_the_pool_under_its_kind_and_slug():
    """No item silently vanishes during flattening (e.g. a malformed row dropped by the reader)."""
    raw = _raw_docs()
    corpus = cp.load_corpus()
    assert set(corpus) == set(raw)
    for key, item in corpus.items():
        kind, slug = key.split(":", 1)
        assert (item.key, item.kind, item.slug) == (key, kind, slug)
        assert kind in (cp.MONEY_MOVES, cp.JOURNEY)


def test_the_eligible_pool_has_a_floor_so_the_corpus_guards_cannot_go_vacuous():
    keys = cp.eligible_keys()
    assert len(keys) >= 25, f"eligible pool shrank to {len(keys)}: {keys}"
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    kinds = [k.split(":", 1)[0] for k in keys]
    # Both pools feed the rotation; `case_story` exists only for Money Moves.
    assert kinds.count(cp.MONEY_MOVES) >= 5 and kinds.count(cp.JOURNEY) >= 5, kinds
    for item in _eligible_items():
        assert item.word_count >= cp.MIN_FACT_WORDS
        assert item.word_count == sum(len(s.split()) for s in item.fact_sentences)
        assert item.ineligible_reason is None


#: Written HERE, independently of `cp.EXCLUDED` (e1 review, idx 30): the policy each key stands
#: for. `test_every_excluded_key_exists…` iterates EXCLUDED itself, so deleting a line deleted
#: its own check — nine of these were deletable with the suite green, and each item clears
#: MIN_FACT_WORDS on its own, i.e. deletion = back in the public rotation.
POLICY_EXCLUSIONS = {
    "money_moves:warren-buffetts-early-days": "named real investor",
    "journey:buffett_way": "named real investor",
    "journey:lynch_way": "named real investor",
    "journey:cathie_wood_way": "named real investor",
    "journey:inversion": "real investor's quotation",
    "money_moves:google-vs-microsoft-ai-wars": "names the model vendor",
    "money_moves:the-future-of-digital-finance": "unsourced statistics",
    "journey:bitcoin_digital_gold": "cryptoasset promotion",
    "journey:tokenomics": "cryptoasset promotion",
    "journey:whale_watching": "FMP-relayed 13F feature",
    "money_moves:the-fall-of-enron": "misconduct, identifiable people",
    "money_moves:the-ftx-collapse": "misconduct, identifiable people",
    "money_moves:theranos-blood-and-lies": "misconduct, identifiable people",
    "money_moves:weworks-unraveling": "founder conduct, identifiable person",
    "money_moves:the-fall-of-sears": "valuation verdict on a named company",
}


def test_every_policy_exclusion_is_still_excluded_and_never_eligible():
    missing = sorted(k for k in POLICY_EXCLUSIONS if k not in cp.EXCLUDED)
    assert not missing, (f"these policy exclusions were removed from EXCLUDED: {missing} — "
                         f"each is a public-surface policy decision, not a tidy-up")
    eligible = set(cp.eligible_keys())
    assert not [k for k in POLICY_EXCLUSIONS if k in eligible]


#: Derived rules, over the SAME flattened sentences the pool reads (a raw-JSON scan hits
#: how-amazon-built-its-moat through its related-articles metadata). Each FAILS on an item it
#: finds that EXCLUDED lacks, forcing a human decision for a NEW article of that kind, instead of
#: silently excluding it (a word in the compliance scan would instead drop fact sentences).
_CRYPTO_RE = re.compile(r"\b(?:bitcoin|btc|ethereum|ether|crypto\w*|cryptocurrenc\w*|stablecoins?|"
                        r"tokens?|tokenomics|blockchain|altcoins?|ftt|defi|nfts?)\b")
_13F_RE = re.compile(r"\b(?:13f|13-f|whales?|superinvestors?)\b")
_MISCONDUCT_RE = re.compile(r"\b(?:fraud\w*|convicted|sentenced|indicted|charged|prison|"
                            r"embezzl\w*|ponzi|scandal|misconduct|deceiv\w*|lied|lies)\b")


def _derived_policy_hits():
    corpus = cp.load_corpus()
    crypto, f13, misconduct = set(), set(), set()
    for key, _kind, _slug, _title, _cat, sents in cp._raw_items():
        folded = [compliance.fold(s) for s in sents]
        if sum(1 for f in folded if _CRYPTO_RE.search(f)) >= 2:
            crypto.add(key)
        if any(_13F_RE.search(f) for f in folded):
            f13.add(key)
        named = sum(1 for _s, code in corpus[key].dropped if code == "person_named")
        if named >= 1 and sum(1 for f in folded if _MISCONDUCT_RE.search(f)) >= 2:
            misconduct.add(key)
    return crypto, f13, misconduct


def test_derived_policy_rules_find_their_items_and_every_one_is_excluded():
    crypto, f13, misconduct = _derived_policy_hits()
    # Anti-vacuity: each rule finds the items the hand list names for that reason.
    assert {"journey:bitcoin_digital_gold", "journey:tokenomics"} <= crypto, crypto
    assert "journey:whale_watching" in f13, f13
    assert {"money_moves:the-fall-of-enron", "money_moves:theranos-blood-and-lies"} <= misconduct
    for rule, keys in (("cryptoasset", crypto), ("13F", f13), ("misconduct", misconduct)):
        not_excluded = sorted(k for k in keys if k not in cp.EXCLUDED)
        assert not not_excluded, (
            f"the {rule} rule found items that are not in EXCLUDED: {not_excluded} — decide "
            f"(and record the reason) before they reach the public rotation"
        )


def test_the_eligible_pool_keeps_at_least_thirty_items():
    """Content-A narrowed the person rules (roles, pronouns, first names) to cost sentences, not
    items: 35 eligible before and after. A future rule that thins the pool below 30 must say why."""
    assert len(cp.eligible_keys()) >= 30, cp.eligible_keys()


# ── people described by role or pronoun (content-A review, idx 0/16) ──────────

#: Written HERE, not imported: a singular role noun or a third-person singular pronoun in a Money
#: Moves case study is one real person.
_ROLE_OR_PRONOUN_RE = re.compile(
    r"\b(?:ceo|chief executive|founder|co-?founder|chairman|chairwoman|boss|president|heir|"
    r"he|him|his|himself|she|her|hers|herself)\b(?![-])(?!s\b)|\bone (?:man|woman|person)\b"
)


def test_no_eligible_money_moves_fact_sentence_points_at_a_person_by_role_or_pronoun():
    hits = [(item.key, s) for item in _eligible_items() if item.kind == cp.MONEY_MOVES
            for s in item.fact_sentences if _ROLE_OR_PRONOUN_RE.search(compliance.fold(s))]
    assert not hits, hits[:10]


@pytest.mark.parametrize("key, fragment", [
    ("money_moves:microsofts-cloud-metamorphosis", "called Linux a cancer"),
    ("money_moves:microsofts-cloud-metamorphosis", "The strategic reframing he made"),
    ("money_moves:tesla-vs-traditional-auto", "production hell"),
    ("money_moves:the-rise-of-lvmh", "What he actually bought"),
    ("money_moves:the-rise-of-lvmh", "The pattern he set"),
    ("money_moves:the-rise-of-lvmh", "one person for three decades"),
])
def test_the_orphans_of_a_dropped_naming_sentence_are_dropped_and_the_item_stays(key, fragment):
    item = cp.load_corpus()[key]
    assert fragment not in item.fact_text, (key, fragment)
    assert any(fragment in s and code == "person_named" for s, code in item.dropped), (key, fragment)
    assert item.eligible, (key, item.ineligible_reason, item.word_count)


def test_journey_keeps_the_fictional_mr_market_pronouns():
    item = cp.load_corpus()["journey:mr_market"]
    assert item.eligible
    assert any(re.search(r"\b(?:he|his|him)\b", s.lower()) for s in item.fact_sentences)


#: The executives the raw bundle names or a model volunteers about those companies — written by
#: hand, NOT read from `compliance.person_lexicon()` (e1 review, idx 31: that list both filters
#: the sheet and judges it, so deleting "bezos" widened both at once and nothing failed).
_FROZEN_EXECUTIVES = (
    "bezos", "jassy", "nadella", "ballmer", "arnault", "morris chang", "lisa su", "huang",
    "tim cook", "steve jobs", "zuckerberg", "musk", "sinegal", "hastings", "iger", "pichai",
    "buffett", "munger",
)


def test_no_eligible_fact_sheet_names_a_frozen_executive():
    rx = re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(n) for n in _FROZEN_EXECUTIVES) + r")(?![a-z])")
    hits = {item.key: m.group(0) for item in _eligible_items()
            for m in [rx.search(compliance.fold(item.fact_text))] if m}
    assert not hits, hits


def test_the_raw_bundle_really_names_the_frozen_executives():
    """Anti-vacuity: the guard above only proves something if the raw corpus names them."""
    raw = " ".join(compliance.fold(leaf) for doc in _raw_docs().values() for leaf in _prose_leaves(doc))
    for name in ("bezos", "nadella", "arnault", "morris chang"):
        assert re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", raw), name


# ── the corpus-wide invariant: prompt, fact sheet and validator agree ─────────


def test_every_eligible_fact_sentence_passes_the_output_scan_with_its_items_rules():
    failures = []
    checked = 0
    for item in _eligible_items():
        strict = cp.strict_instruments(item.kind)
        for s in item.fact_sentences:
            checked += 1
            # allow_emoji=False: the SHARED fields (hook/script/cards/slides) are scanned that
            # way, and a sentence the writer copies there must pass there too.
            vs = compliance.scan_text("fact", s, allow_emoji=False, strict_instruments=strict,
                                      company_terms=item.company_terms)
            vs += check_grounding("fact", s, item.grounding)
            if vs:
                failures.append((item.key, s, [(v.code, v.detail) for v in vs]))
    assert checked >= 25 * 20, checked  # anti-vacuity: hundreds of sentences, not a handful
    assert not failures, failures[:10]


def test_strict_instruments_is_off_only_for_journey():
    assert cp.strict_instruments(cp.JOURNEY) is False
    assert cp.strict_instruments(cp.MONEY_MOVES) is True
    # Fail-closed for anything unexpected.
    assert cp.strict_instruments("") is True
    assert cp.strict_instruments("something_new") is True


def test_journey_items_carry_no_company_terms_and_money_moves_items_name_their_companies():
    corpus = cp.load_corpus()
    for item in corpus.values():
        if item.kind == cp.JOURNEY:
            assert item.company_terms == frozenset(), item.key
    # A handful of well-known case-study names must register as company terms.
    assert "costco" in corpus["money_moves:costcos-membership-magic"].company_terms
    assert "visa" in corpus["money_moves:visa-vs-mastercard"].company_terms
    assert "mastercard" in corpus["money_moves:visa-vs-mastercard"].company_terms


def test_no_eligible_fact_sentence_carries_markup_readalong_icons_or_quote_blocks():
    raw = _raw_docs()
    problems = []
    for item in _eligible_items():
        doc = raw[item.key]
        folded_sheet = compliance.fold(item.fact_text)
        folded_sentences = {compliance.fold(s) for s in item.fact_sentences}

        for s in item.fact_sentences:
            if "**" in s or "__" in s:
                problems.append((item.key, "bold markup", s))
            for artefact in ("readAlong", "itemsReadAlong", "readAlongWords", "audioClip",
                             ".m4a", "'start'", '"start"', "{'text'", '{"text"'):
                if artefact in s:
                    problems.append((item.key, artefact, s))

        for block in _quote_blocks(doc):
            attribution = compliance.fold(str(block.get("attribution") or "")).strip(" -—")
            if attribution and attribution in folded_sheet:
                problems.append((item.key, "quote attribution", attribution))
            for q in compliance.sentences(str(block.get("text") or "")):
                q_folded = compliance.fold(cp._as_sentence(q))
                if len(q_folded.split()) >= 4 and q_folded in folded_sentences:
                    problems.append((item.key, "quote text", q_folded))

        for icon in _values_for_key(doc, "icon"):
            # SF-symbol identifiers ("bitcoinsign.circle.fill"). A one-word icon such as
            # "percent" is also an English word the prose may legitimately use.
            if isinstance(icon, str) and "." in icon and icon.lower() in folded_sheet:
                problems.append((item.key, "icon", icon))
        for cta in _values_for_key(doc, "cta"):
            if isinstance(cta, str) and cta and re.search(rf"\b{re.escape(cta.lower())}\b", folded_sheet):
                problems.append((item.key, "cta id", cta))
    assert not problems, problems[:10]


def test_the_real_bundle_does_contain_quote_blocks_on_eligible_items():
    """Anti-vacuity for the test above: the quote-block check only proves something if at least
    one ELIGIBLE item really has a quote block with an attribution."""
    raw = _raw_docs()
    with_quotes = [k for k in cp.eligible_keys() if any(b.get("attribution") for b in _quote_blocks(raw[k]))]
    assert with_quotes, "no eligible item has a quote block — the quote-strip guard is vacuous"
    with_icons = [k for k in cp.eligible_keys()
                  if any(isinstance(i, str) and "." in i for i in _values_for_key(raw[k], "icon"))]
    assert with_icons, "no eligible item has an SF-symbol icon — the icon guard is vacuous"


def test_fact_sentences_are_not_split_at_abbreviations():
    fragment = re.compile(r"(?:^|\s)(?:Mr|Mrs|Ms|Dr|St|Inc|Co|Corp|Ltd|vs)\.$")
    broken = [(item.key, s) for item in _eligible_items() for s in item.fact_sentences
              if fragment.search(s)]
    assert not broken, broken[:10]


# ── derived exclusion rule: an item built on a listed investor is excluded ────


def _investor_re() -> re.Pattern:
    """An App-Store-listed investor, by full name — or by surname where compliance already
    treats that surname as unambiguous (never "wood", "marks", "lynch", "graham")."""
    alts = set()
    for name in compliance.APP_STORE_NAMES:
        alts.add(name)
        surname = name.split()[-1]
        if surname in compliance.UNAMBIGUOUS_SURNAMES:
            alts.add(surname)
    body = "|".join(re.escape(a) for a in sorted(alts, key=len, reverse=True))
    return re.compile(r"(?<![a-z0-9])(?:" + body + r")(?![a-z0-9])")


def test_items_naming_a_listed_investor_in_two_or_more_raw_sentences_are_excluded():
    rx = _investor_re()
    heavy = {}
    for key, doc in _raw_docs().items():
        n = sum(
            1
            for leaf in _prose_leaves(doc)
            for sent in compliance.sentences(leaf)
            if rx.search(compliance.fold(sent))
        )
        if n >= 2:
            heavy[key] = n
    # Anti-vacuity: the biography and the method lessons really do name their investor.
    assert "money_moves:warren-buffetts-early-days" in heavy, heavy
    assert len(heavy) >= 3, heavy
    not_excluded = {k: n for k, n in heavy.items() if k not in cp.EXCLUDED}
    assert not not_excluded, (
        f"these items name an App-Store-listed investor in >= 2 sentences but are not in "
        f"EXCLUDED: {not_excluded}"
    )
    corpus = cp.load_corpus()
    assert all(not corpus[k].eligible for k in heavy)


def test_no_eligible_fact_sheet_names_anyone_on_the_person_lexicon():
    """Belt and braces over the scan invariant: the whole lexicon, word-bounded, over the
    folded fact sheet (catches a name split across two sentences of one sheet)."""
    names = [n for n in compliance.person_lexicon() if n]
    rx = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(n) for n in names) + r")(?![a-z0-9])")
    hits = {}
    for item in _eligible_items():
        m = rx.search(compliance.fold(item.fact_text.replace("\n", " ")))
        if m:
            hits[item.key] = m.group(0)
    assert not hits, hits


# ── flattening, on synthetic documents with a sentinel in every unwanted field ─


_ARTICLE_SENTINELS = (
    "zqxquote", "zqxattribution", "zqxreadalong", "zqxitemsreadalong", "zqxicon",
    "zqxtrendvalue", "zqxauthor", "zqxgradient", "zqxtag", "zqxaudio",
)
_LESSON_SENTINELS = ("zqxwords", "zqxclip", "zqxctaid", "zqxlessoncategory")


def _synthetic_article(slug: str = "synthetic-warehouse", paragraphs: int = 12) -> Dict[str, Any]:
    body = [
        {"type": "paragraph",
         "text": "The **membership fee** is the real product of the warehouse club.",
         "readAlong": [{"text": "zqxreadalong spoken words", "start": 0.0, "end": 1.0}]},
        {"type": "quote", "text": "zqxquote is what a famous founder once said about shopping.",
         "attribution": "— Zqxattribution Person", "readAlong": []},
        {"type": "callout", "icon": "zqxicon.circle.fill", "style": "info",
         "text": "Members renew because the savings are easy to see.",
         "readAlong": []},
        {"type": "bulletList", "items": ["Bulk packs lower the cost per unit.", "Few brands per aisle."],
         "itemsReadAlong": [[{"text": "zqxitemsreadalong", "start": 0, "end": 1}]]},
    ]
    body += [{"type": "paragraph",
              "text": "Shoppers drive a long way to fill a cart with household basics every month.",
              "readAlong": []}] * paragraphs
    return {
        "slug": slug, "title": "Warehouse club lessons", "subtitle": "How a fee builds loyalty",
        "cardSubtitle": "", "category": "blueprints", "author": "zqxauthor",
        "tagLabel": "zqxtag", "audioUrl": "https://example.invalid/zqxaudio.m4a",
        "heroGradientColors": ["#zqxgradient"],
        "keyHighlights": [{"icon": "zqxicon.star", "title": "Loyal members",
                           "description": "Renewal rates stay high year after year."}],
        "statistics": [{"value": "90%", "label": "Renewal rate", "trend": "up",
                        "trendValue": "zqxtrendvalue"}],
        "sections": [{"title": "The fee", "icon": "zqxicon.section", "hasGlowEffect": True,
                      "content": body}],
    }


def _synthetic_lesson(slug: str = "synthetic_lesson") -> Dict[str, Any]:
    card_text = "Patience lets a small habit grow into a large result over many years."
    return {
        "slug": slug, "title": "Quiet habits", "level": "foundation",
        "category": "zqxlessoncategory", "description": "Why **small** steps add up.",
        "cards": [
            {"type": "title", "headline": "The **quiet** habit", "text": card_text,
             "audioClip": "zqxclip_01", "hasImage": True,
             "readAlongWords": [{"text": "zqxwords", "start": 0.1, "end": 0.2}]},
            *[{"type": "content", "text": card_text, "audioClip": "zqxclip_02",
               "readAlongWords": [{"text": "zqxwords", "start": 0.1, "end": 0.2}]}] * 14,
            {"type": "completion", "headline": "Well done.",
             "text": "Keep the habit going for one more week.", "cta": "zqxctaid",
             "hasImage": True},
        ],
    }


def test_money_moves_flattening_drops_every_unwanted_field():
    sents = cp._split(cp._money_moves_blocks(_synthetic_article()))
    text = "\n".join(sents).lower()
    for sentinel in _ARTICLE_SENTINELS:
        assert sentinel not in text, sentinel
    assert "**" not in text and "__" not in text
    # Non-vacuity: the prose around each dropped field survived.
    assert "the membership fee is the real product of the warehouse club." in text
    assert "members renew because the savings are easy to see." in text
    assert "bulk packs lower the cost per unit." in text
    assert "renewal rate: 90%." in text
    assert "loyal members: renewal rates stay high year after year." in text
    assert "the fee." in text  # section title
    assert all(s.endswith((".", "!", "?")) for s in sents), sents


def test_journey_flattening_drops_every_unwanted_field():
    sents = cp._split(cp._journey_blocks(_synthetic_lesson()))
    text = "\n".join(sents).lower()
    for sentinel in _LESSON_SENTINELS:
        assert sentinel not in text, sentinel
    assert "**" not in text
    assert "the quiet habit." in text and "why small steps add up." in text
    assert "keep the habit going for one more week." in text


def test_flattening_tolerates_malformed_blocks():
    article = {
        "slug": "x", "title": "Odd shapes",
        "keyHighlights": ["not a dict", {"title": None, "description": None}, {"title": "Only title"}],
        "statistics": [{"value": "", "label": "no value"}, "junk", {"value": "5", "label": None}],
        "sections": ["junk", {"content": ["junk", {"type": "bulletList", "items": None},
                                          {"type": "paragraph"}, {"type": "quote"}]}],
    }
    assert cp._split(cp._money_moves_blocks(article)) == ["Odd shapes.", "Only title."]
    lesson = {"slug": "y", "cards": [None, "junk", {"type": "content"}, {"headline": "Hi"}]}
    assert cp._split(cp._journey_blocks(lesson)) == ["Hi."]


def test_a_synthetic_corpus_end_to_end_keeps_sentinels_out_of_the_fact_sheet(tmp_path, swap_corpus):
    swap_corpus(
        _write(tmp_path / "mm.json", {"articles": [_synthetic_article()]}),
        _write(tmp_path / "jr.json", {"lessons": [_synthetic_lesson()]}),
    )
    corpus = cp.load_corpus()
    assert set(corpus) == {"money_moves:synthetic-warehouse", "journey:synthetic_lesson"}
    article = corpus["money_moves:synthetic-warehouse"]
    lesson = corpus["journey:synthetic_lesson"]
    for item, sentinels in ((article, _ARTICLE_SENTINELS), (lesson, _LESSON_SENTINELS)):
        sheet = item.fact_text.lower()
        dropped = " ".join(s for s, _ in item.dropped).lower()
        for sentinel in sentinels:
            assert sentinel not in sheet and sentinel not in dropped, (item.key, sentinel)
        assert "**" not in sheet
        assert item.eligible, (item.key, item.ineligible_reason, item.word_count)
    assert article.category == "blueprints" and lesson.category == "foundation"


# ── company terms ─────────────────────────────────────────────────────────────


def _vocab_for(sents: List[str], extra_lower_words: str = ""):
    vocab = cp._vocab(sents + ([extra_lower_words] if extra_lower_words else []))
    base = full_vocab(vocab)
    return base, vocab_roots(base)


def test_company_terms_catch_word_like_names_written_capitalised_mid_sentence():
    # Invented chains named with ordinary words, NOT in the company lexicon, so only the
    # mid-sentence-capitals rule can make any of them a term (content-B: a lexicon company is a
    # term however often it appears — see the next test).
    sents = [
        "For decades, shoppers trusted Beacon for tools and appliances.",
        "Almost every town had a Beacon on its main street.",
        "Families loved Beacon's catalog.",
        "Store hours changed often.",          # sentence-initial only: not a name
        "Store staff smiled at every visitor.",
        "Many shoppers compared prices at Harbor once.",   # mid-sentence ONCE: not enough
        "An Maple a day is a saying.",          # mid-caps twice below, but also lower case
        "They bought an Maple phone and an maple pie.",
    ]
    base, roots = _vocab_for(sents, "the beacon store harbor maple")
    for word in ("beacon", "store", "harbor", "maple"):
        assert ordinary(word, base, roots), word
        assert word not in compliance.company_lexicon().distinct
        assert word.capitalize() not in compliance.company_lexicon().words
    terms = cp.proper_nouns(sents, base, roots)
    assert "beacon" in terms           # 3 mid-sentence capitals, never lower case in the item
    assert "store" not in terms        # only ever capitalised at the start of a sentence
    assert "harbor" not in terms       # once
    assert "maple" not in terms        # the item also writes it in lower case


def test_a_lexicon_company_is_a_term_even_once_or_beside_its_lower_case_word():
    """content-B review (idx 47): the item's companies must never depend on how often a heading
    capitalises them. "Target" once and "Apple" beside "apple pie" are still the companies."""
    sents = [
        "Many shoppers compared prices at Target once.",
        "They bought an Apple phone and an apple pie.",
    ]
    base, roots = _vocab_for(sents, "the target apple")
    terms = cp.proper_nouns(sents, base, roots)
    assert {"target", "apple"} <= terms


def test_a_word_the_item_writes_in_a_compound_or_a_plural_is_not_a_company_term():
    """idx 47: "high-margin" and "margins" prove "Margin" is the ordinary word, even where the
    item capitalises it mid-sentence twice outside any heading."""
    sents = [
        "Investors watched the Margin closely.",
        "The Margin story mattered.",
        "It was a high-margin business.",
        "Its margins grew.",
    ]
    base, roots = _vocab_for(sents, "the margin")
    assert "margin" not in cp.proper_nouns(sents, base, roots)
    assert "margin" in cp.proper_nouns(sents[:2], base, roots)       # anti-vacuity


def test_heading_words_are_not_company_terms_and_the_real_companies_stay():
    """idx 47: "Margin", "Launches" and "March" were issuers because a heading or a month wrote
    them capitalised; "Cheaper batteries widened the margin." was then a verdict on Tesla."""
    corpus = cp.load_corpus()
    assert "margin" not in corpus["money_moves:tesla-vs-traditional-auto"].company_terms
    assert "launches" not in corpus["money_moves:the-rise-of-tiktok-vs-instagram-reels"].company_terms
    assert "march" not in corpus["money_moves:boeing-vs-airbus-the-aerospace-duopoly"].company_terms
    for key, name in (("money_moves:metas-metaverse-pivot", "meta"),
                      ("money_moves:the-home-depot-vs-lowes", "depot"),
                      ("money_moves:apples-services-revolution", "apple"),
                      ("money_moves:visa-vs-mastercard", "visa"),
                      ("money_moves:tesla-vs-traditional-auto", "tesla")):
        assert name in corpus[key].company_terms, (key, name)
    tesla = corpus["money_moves:tesla-vs-traditional-auto"]
    assert compliance.scan_text("x", "Cheaper batteries widened the margin.",
                                strict_instruments=True, company_terms=tesla.company_terms) == []


def test_company_terms_include_non_words_even_at_a_sentence_start():
    sents = ["Zqxmart opened its first store in a small town."]
    base, roots = _vocab_for(sents)
    assert "zqxmart" in cp.proper_nouns(sents, base, roots)


def test_a_money_moves_company_term_makes_a_tier2_word_a_violation_end_to_end(tmp_path, swap_corpus):
    """The point of `company_terms`: "Sears was a bargain back then" carries no instrument word,
    but in a Money Moves item that names Sears it is a verdict on an issuer and must be dropped.

    content-B review (idx 4/11/12): a Journey lesson that NAMES a company drops it too — Sears is
    in the company lexicon, and a sentence naming a company gets the full class-B rules in every
    mode. It used to be kept, on the premise that a Journey sheet names no instrument. The
    generic version, with no company, is still Journey copy."""
    extra = [
        "Shoppers trusted Sears for tools and appliances for many decades.",
        "Almost every town had a Sears on its main street in those years.",
        "Sears was a bargain back then.",
        "Everything looked like a bargain back then.",
    ]
    article = _synthetic_article("synthetic-sears")
    article["sections"][0]["content"] = (
        [{"type": "paragraph", "text": t} for t in extra] + article["sections"][0]["content"]
    )
    lesson = _synthetic_lesson("synthetic_sears")
    lesson["cards"] = [{"type": "content", "text": t} for t in extra] + lesson["cards"]
    swap_corpus(
        _write(tmp_path / "mm.json", {"articles": [article]}),
        _write(tmp_path / "jr.json", {"lessons": [lesson]}),
    )
    corpus = cp.load_corpus()
    mm, jr = corpus["money_moves:synthetic-sears"], corpus["journey:synthetic_sears"]
    assert "sears" in mm.company_terms and jr.company_terms == frozenset()
    assert ("Sears was a bargain back then.", "class_b_evaluative") in mm.dropped
    assert "Sears was a bargain back then." not in mm.fact_sentences
    assert ("Sears was a bargain back then.", "class_b_evaluative") in jr.dropped
    assert "Sears was a bargain back then." not in jr.fact_sentences
    assert "Everything looked like a bargain back then." in jr.fact_sentences


# ── eligibility threshold ─────────────────────────────────────────────────────


@pytest.mark.parametrize("extra_words, eligible", [(0, True), (-1, False)])
def test_min_fact_words_boundary(tmp_path, swap_corpus, extra_words, eligible):
    ten = "Members pay a yearly fee to shop in the warehouse."   # 10 words
    eight = "Members pay a yearly fee to shop there."             # 8 words
    seven = "Members pay a yearly fee to shop."                    # 7 words
    # title (2) + 14 x 10 + 8 = 150 exactly; swapping the 8 for a 7 gives 149.
    last = eight if extra_words == 0 else seven
    article = {"slug": "boundary", "title": "Warehouse lessons", "category": "blueprints",
               "sections": [{"content": [{"type": "paragraph", "text": ten}] * 14
                             + [{"type": "paragraph", "text": last}]}]}
    swap_corpus(_write(tmp_path / "mm.json", {"articles": [article]}),
                _write(tmp_path / "jr.json", {"lessons": []}))
    item = cp.load_corpus()["money_moves:boundary"]
    assert not item.dropped, item.dropped
    assert item.word_count == cp.MIN_FACT_WORDS + extra_words
    assert item.eligible is eligible
    if not eligible:
        assert "too thin" in (item.ineligible_reason or "")
        assert cp.eligible_keys() == []
    else:
        assert cp.eligible_keys() == ["money_moves:boundary"]


# ── degraded corpus: empty pool + ERROR, never an exception ───────────────────


def test_an_unreadable_corpus_yields_an_empty_pool_and_an_error(tmp_path, swap_corpus, caplog):
    swap_corpus(tmp_path / "missing_mm.json", tmp_path / "missing_jr.json")
    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        assert cp.load_corpus() == {}
        assert cp.eligible_keys() == []
        assert cp.get_item("journey:mr_market") is None
    errors = [r for r in caplog.records if r.name == _LOGGER and r.levelno >= logging.ERROR]
    text = " ".join(r.getMessage() for r in errors)
    assert "missing_mm.json" in text and "missing_jr.json" in text, text
    assert "NO items" in text


def test_invalid_json_is_an_error_not_an_exception(tmp_path, swap_corpus, caplog):
    swap_corpus(_write(tmp_path / "mm.json", "{not json"), _write(tmp_path / "jr.json", ""))
    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        assert cp.load_corpus() == {}
    text = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert "mm.json" in text and "jr.json" in text
    assert "JSONDecodeError" in text or "ValueError" in text


def test_one_unreadable_file_leaves_the_other_half_of_the_pool(tmp_path, swap_corpus, caplog):
    swap_corpus(tmp_path / "gone.json",
                _write(tmp_path / "jr.json", {"lessons": [_synthetic_lesson()]}))
    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        corpus = cp.load_corpus()
    assert set(corpus) == {"journey:synthetic_lesson"}
    assert any("gone.json" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)


@pytest.mark.parametrize("payload", [[{"slug": "a"}], {"items": []}, {"articles": "oops"}])
def test_a_wrong_shaped_corpus_file_is_logged(tmp_path, swap_corpus, caplog, payload):
    swap_corpus(_write(tmp_path / "wrong_shape.json", payload),
                _write(tmp_path / "jr.json", {"lessons": [_synthetic_lesson()]}))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        corpus = cp.load_corpus()
    assert set(corpus) == {"journey:synthetic_lesson"}
    assert any("wrong_shape.json" in r.getMessage()
               for r in caplog.records if r.levelno >= logging.WARNING), caplog.text


def test_rows_without_a_slug_are_skipped_not_fatal(tmp_path, swap_corpus):
    swap_corpus(
        _write(tmp_path / "mm.json", {"articles": [{"title": "no slug"}, "junk", None,
                                                   _synthetic_article()]}),
        _write(tmp_path / "jr.json", {"lessons": [{"slug": ""}, _synthetic_lesson()]}),
    )
    assert set(cp.load_corpus()) == {"money_moves:synthetic-warehouse", "journey:synthetic_lesson"}


def test_the_real_corpus_is_back_after_a_swap():
    """Runs after the swapping tests in file order: the fixture's cache_clear restored the real
    pool (a leaked synthetic corpus would break every other marketing test in the session)."""
    assert len(cp.eligible_keys()) >= 25
    assert cp.get_item("journey:mr_market") is not None
