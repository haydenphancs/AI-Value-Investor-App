"""
The company lexicon and the entity rules around it (content-B review, idx 4/11/12/34/47).

A company whose name is an English word ("Apple", "Target", "Visa", "Coke") passed grounding as
ordinary vocabulary, so a Journey post could name one and give it a valuation opinion. The fix
is `data/known_companies_en.txt` + `compliance.sentence_company_mentions` (a company only in a
NAME position) + `grounding._check_companies` (a company must be one the item's own sheet names).
These tests pin the lexicon's marking, the position rules both ways, the grounding rule, and the
two entity knobs a one-token edit could turn off (`_PREFIX_MIN`, the hyphen-compound all()).

Category 1 (pure): no network, no Supabase.
"""

from __future__ import annotations

from typing import List

import pytest

from app.services.marketing import compliance as c
from app.services.marketing import content_pool as cp
from app.services.marketing import grounding as g


def _mentions(text: str) -> List[str]:
    return c.company_mentions(c.skeleton(c.clean(text)))


# ── the lexicon file ─────────────────────────────────────────────────────────


def test_the_lexicon_is_vendored_and_non_trivial():
    lex = c.company_lexicon()
    assert len(lex.words) >= 40 and len(lex.distinct) >= 100
    assert len(lex.caps) >= 10 and len(lex.multi) >= 20
    for word in ("Apple", "Target", "Visa", "Oracle", "Coke", "Chevron", "Meta", "Sears", "Kodak"):
        assert word in lex.words, word
    for name in ("nvidia", "costco", "amazon", "tesla", "microsoft", "coca-cola", "walmart"):
        assert name in lex.distinct, name
    assert ("Home", "Depot") in lex.multi and "IBM" in lex.caps


@pytest.fixture(scope="module")
def tiny_ctx() -> g.GroundingContext:
    return g.build_context(("Members pay a fee.",), frozenset({"members", "pay", "a", "fee"}))


def test_every_word_brand_is_an_english_word_and_every_other_name_is_not(tiny_ctx):
    """The "~" marking decides the matching rule (a word-brand needs a name position; any other
    name matches anywhere). It must agree with the grounding dictionary: a "~" entry that is not
    a word would be under-matched, and an unmarked word would fire on ordinary text."""
    lex = c.company_lexicon()
    not_words = sorted(w for w in lex.words if not g.is_ordinary_word(w.lower(), tiny_ctx))
    assert not_words == [], not_words
    words = sorted(n for n in lex.distinct if "-" not in n and g.is_ordinary_word(n, tiny_ctx))
    assert words == [], words


#: Every company the Learn corpus names (all items, eligible or not), written by hand — not read
#: from the lexicon, which would make the check circular.
_CORPUS_COMPANIES = (
    "Airbus", "Amazon", "AMD", "Apple", "Boeing", "Bulgari", "ByteDance", "Celine", "Coca-Cola",
    "Costco", "Dior", "Disney", "Enron", "Facebook", "Fendi", "Ford", "GitHub", "GM", "Google",
    "Home Depot", "Instagram", "Intel", "Kmart", "Loewe", "Lowe", "LVMH", "Mastercard",
    "Mellanox", "Meta", "Microsoft", "Netflix", "NVIDIA", "Pixar", "Sears", "SoftBank",
    "Tesla", "Theranos", "TikTok", "Toyota", "TSMC", "Visa", "Volkswagen", "WeWork",
)


@pytest.mark.parametrize("name", _CORPUS_COMPANIES)
def test_every_company_the_corpus_names_is_in_the_lexicon(name):
    assert _mentions(f"Analysts studied {name} for years.") == [name.lower()], name


# ── positions: a word-brand is a company only where a name goes ──────────────


@pytest.mark.parametrize("text, name", [
    ("Companies like Apple have wide moats.", "apple"),         # mid-sentence capital
    ("Apple's stock looks cheap.", "apple"),                    # possessive
    ("Target shares are a bargain.", "target"),                 # before shares
    ("Apple has a wide moat.", "apple"),                        # subject of a verb
    ("Target is expensive.", "target"),
    ("Kodak once looked like a bargain.", "kodak"),
    ("Visa and Mastercard dominate.", "visa"),
    ("Take Apple: its market cap is big.", "apple"),
    ("Why Apple Stock Looks Cheap", "apple"),                   # Title Case, before "Stock"
    ("Why Apple Looks Cheap", "apple"),                         # Title Case, before a verb
    ("Target, the retailer, looks cheap.", "target"),           # apposition
    ("NVIDIA grew.", "nvidia"),
    ("nvidia is a buy.", "nvidia"),                             # a non-word name in lower case
    ("Home Depot sells tools.", "home depot"),
    ("IBM sold its PC arm.", "ibm"),
])
def test_a_company_in_a_name_position_is_found(text, name):
    assert name in _mentions(text), (text, _mentions(text))


@pytest.mark.parametrize("text", [
    "Target a savings rate you can keep.",          # an imperative verb
    "Chase returns, and fear usually follows.",
    "Shell out less on fees.",
    "Discover how a moat protects profits.",
    "Block out the noise.",
    "An apple a day is a saying.",                  # lower case
    "Why Target Dates Matter for Retirement",      # Title Case, no name context
    "Mr. Market offers you a price.",
    "The oracle of the market spoke.",
    "Square one is a fine place to start.",
    "Mr. Ford explained it to the class.",          # a person: the person rules own it
])
def test_the_same_word_as_ordinary_english_is_not_a_company(text):
    assert _mentions(text) == [], (text, _mentions(text))


# ── grounding: a company must be one the item's sheet names ──────────────────


def _ground(sheet, text):
    ctx = g.build_context(sheet, frozenset({"the", "a", "apples", "to", "compare"}))
    return [(v.code, v.detail) for v in g.check_grounding("f", c.clean(text), ctx)]


def test_a_lower_case_word_in_the_sheet_does_not_ground_the_company():
    """journey:key_statistics says "compare apples to apples": that is not a sheet naming Apple."""
    sheet = ("Compare apples to apples when you read a ratio.",)
    assert ("ungrounded_entity", "Apple") in _ground(sheet, "Apple trades at a high ratio.")


def test_a_company_the_sheet_names_is_grounded():
    sheet = ("Shoppers compared Apple with its rivals for years.",)
    assert _ground(sheet, "Apple built a loyal base.") == []


def test_a_company_the_sheet_writes_capitalised_anywhere_is_grounded():
    """The sheet is our own text: a name it writes capitalised in a position the detector does
    not count ("Apple moving its chips…" opens a sentence without a listed verb) still grounds
    it — "TSMC won Apple's business" is the case study's own fact, not an added company."""
    sheet = ("Apple moving its chips to TSMC was a turning point.",)
    assert c.company_mentions(c.skeleton(sheet[0])) == ["tsmc"]
    assert _ground(sheet, "TSMC won Apple's business.") == []


def test_a_hyphenated_or_lower_case_company_is_checked_too():
    sheet = ("Brands build moats over decades.",)
    assert ("ungrounded_entity", "Coca-Cola") in _ground(sheet, "Coca-Cola built a moat.")
    assert ("ungrounded_entity", "nvidia") in _ground(sheet, "Brands like nvidia build moats.")


def test_a_non_word_company_is_reported_once():
    sheet = ("Brands build moats over decades.",)
    got = _ground(sheet, "Walmart built a moat.")
    assert got.count(("ungrounded_entity", "Walmart")) == 1, got


def test_the_journey_sheets_name_no_company():
    """The corpus fact behind the Journey rule: no eligible lesson names a lexicon company, so
    any company in a Journey post is ungrounded."""
    named = {item.key: sorted(item.grounding.company_names & (c.company_lexicon().distinct
                                                               | {w.lower() for w in c.company_lexicon().words}))
             for item in (cp.get_item(k) for k in cp.eligible_keys()) if item.kind == cp.JOURNEY}
    assert all(not v for v in named.values()), {k: v for k, v in named.items() if v}


# ── idx 34: the two entity knobs a one-token edit could turn off ─────────────

#: A hand-written sheet that names neither the probes below nor anything like them.
_SHEET = ("Costco charges members an annual fee.", "Warehouse clubs sell in bulk.")
_VOCAB = frozenset("costco charges members an annual fee warehouse clubs sell in bulk".split())


@pytest.fixture(scope="module")
def ctx34() -> g.GroundingContext:
    return g.build_context(_SHEET, _VOCAB)


@pytest.mark.parametrize("text, name", [
    ("It was never a Zorbex-style price war.", "Zorbex-style"),   # not a lexicon company
    ("It was never a Walmart-style price war.", "Walmart-style"),
    ("An Amazon-sized base.", "Amazon-sized"),
    ("Nvidia-like margins came later.", "Nvidia-like"),
])
def test_a_hyphen_compound_needs_every_part_grounded(ctx34, text, name):
    """`all(...)` over the parts: `any(...)` would let "Walmart-style" through on "style"."""
    for part in name.lower().split("-")[:1]:
        assert part not in ctx34.tokens and part not in ctx34.vocab
    assert ("ungrounded_entity", name) in [(v.code, v.detail)
                                           for v in g.check_grounding("f", text, ctx34)]


@pytest.mark.parametrize("text", ["A Costco-style fee.", "A Warehouse-style fee."])
def test_a_hyphen_compound_of_grounded_or_ordinary_parts_passes(ctx34, text):
    assert g.check_grounding("f", text, ctx34) == []


@pytest.mark.parametrize("word", ["amazon", "schwab", "intel"])
def test_the_prefix_rule_does_not_make_a_short_root_ordinary(ctx34, word):
    """_PREFIX_MIN lower bound: at 6 "amazon" ("amazonite") and "schwab" become words, at 5
    "intel" ("intellect"). Tested on `ordinary()` itself, so the company lexicon cannot mask it."""
    assert word not in ctx34.tokens and word not in ctx34.vocab
    assert not g.ordinary(word, ctx34.vocab, ctx34.vocab_roots), word


@pytest.mark.parametrize("word", ["timeline", "closest"])
def test_the_prefix_rule_still_makes_a_seven_letter_root_ordinary(word):
    """_PREFIX_MIN upper bound, and the rule's positive coverage: these are ordinary ONLY through
    a 7-character prefix of a dictionary root (8 — or no prefix rule — makes them names)."""
    vocab = g.full_vocab(frozenset({"the", "a"}))
    assert g.ordinary(word, vocab, g.vocab_roots(vocab)), word
    ctx = g.build_context(("The plan is simple.",), frozenset({"the", "plan", "is", "simple"}))
    assert g.check_grounding("f", word.capitalize() + " matters.", ctx) == []
