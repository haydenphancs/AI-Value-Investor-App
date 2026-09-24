"""
Adversarial tests for the public-copy compliance scan (`app/services/marketing/compliance.py`).

Written against the promises in the module docstring, `.claude/rules/marketing.md` §1 and the
Phase 2 plan: no real person (named, nicknamed, hashtagged or split by an invisible character),
no value/price opinion on an instrument (class B), no advice directive, no % return figure, no
vendor name, no code-owned brand/CTA/disclaimer text, no link/handle/markup/cashtag — and a scan
that stays linear on a 100k-char repetition loop. A test marked `# BUG:` asserts the promised
behaviour and fails today; it stays failing until the source is fixed.

Every input goes through `clean()` first, exactly as `writer_service` does: the stored text IS
the scanned text.

Category 1 (pure): no network, no Supabase; reads only the bundled data files.
"""

from __future__ import annotations

import importlib.util
import json
import re
import time
from pathlib import Path

import pytest

from app.services.marketing import compliance as c
from app.services.marketing.compliance import clean, fold, scan_text
from app.services.marketing.grounding import build_context, check_grounding

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND / "data"


def scan(text, **kw):
    return scan_text("f", clean(text), **kw)


def codes(text, **kw):
    return [v.code for v in scan(text, **kw)]


def person_hits(text):
    return [v.detail for v in scan(text) if v.code == "person_named"]


def _load_misattribution_module():
    path = BACKEND / "tests" / "test_learn_content_misattributions.py"
    spec = importlib.util.spec_from_file_location("_marketing_misattr_source", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── real people ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "Warren Buffett built a habit.",
    "WARREN BUFFETT built a habit.",
    "Buffett built a habit.",
    "Buffett's habit.",
    "Buffett\u2019s habit.",
    "Peter Lynch wrote books.",
    "Cathie Wood runs a fund.",
    "Sam Bankman-Fried ran an exchange.",
    "bankman-fried ran it.",
    "Bankman\u2013Fried ran it.",            # en dash folds to a hyphen
    "Tim Cook runs a company.",
    "Steve Jobs built it.",
    "Lisa Su led the turnaround.",
    "Elizabeth Holmes.",
    "W. Buffett.",
    "Warren\nBuffett.",                     # split across a line break
    "Warren  \n  Buffett.",
    "Warren\u00a0Buffett.",
    "War\u200bren Buf\u200bfett.",          # zero-width inside the name
    "Buf\u2060fett.",
    "Buf\ufefffett.",
    "Buf\u202efett.",
    "\uff22\uff55\uff46\uff46\uff45\uff54\uff54.",  # fullwidth, NFKC-folded
    "#warrenbuffett",
    "#WarrenBuffett",
    "#buffett",
    "@warrenbuffett",
    "@Warren_Buffett",
    "the Oracle of Omaha",
    "the Sage of Omaha",
    "a legendary investor",
    "a billionaire investor",
    "the famous investor",
])
def test_person_is_flagged(text):
    assert person_hits(text), text


@pytest.mark.parametrize("surname", c.UNAMBIGUOUS_SURNAMES)
def test_every_unambiguous_surname_is_flagged_alone(surname):
    assert person_hits(f"{surname.title()} said so.")


@pytest.mark.parametrize("name", c.APP_STORE_NAMES + c.CORPUS_PEOPLE)
def test_every_listed_full_name_is_flagged(name):
    assert person_hits(f"{name.title()} said so.")


@pytest.mark.parametrize("descriptor", c.PERSON_DESCRIPTORS)
def test_every_descriptor_is_flagged(descriptor):
    assert person_hits(f"As {descriptor} once said.")


def _registry_people():
    rows = json.loads((DATA / "whale_registry.json").read_text(encoding="utf-8"))
    return sorted(r["name"] for r in rows if r.get("category") in ("investors", "politicians"))


def _quote_authors():
    data = json.loads((DATA / "weekly_investor_quotes.json").read_text(encoding="utf-8"))
    return sorted({q["author"] for q in data["quotes"]})


def test_the_data_sources_are_non_trivial():
    assert len(_registry_people()) >= 30
    assert len(_quote_authors()) >= 10


@pytest.mark.parametrize("name", _registry_people())
def test_whale_registry_individuals_are_flagged(name):
    assert person_hits(f"{name} bought shares.")


@pytest.mark.parametrize("author", _quote_authors())
def test_investor_quote_authors_are_flagged(author):
    assert person_hits(f"{author} wrote a book.")


@pytest.mark.parametrize("author", [a for a in _quote_authors() if re.search(r"\b[A-Z]\.", a)])
def test_quote_authors_without_their_middle_initial_are_flagged(author):
    bare = re.sub(r"\s+[A-Z]\.(?=\s)", "", author)
    assert person_hits(f"{bare} wrote a book.")


def test_quote_author_without_generational_suffix_is_flagged():
    assert person_hits("Thomas Rowe Price founded a firm.")


@pytest.mark.parametrize("text", [
    "Merrill Lynch is a brokerage.",
    "T. Rowe Price Group is a firm.",
    "Cook the books.",
    "The jobs report.",
    "Sherlock Holmes.",
    "Price is what you pay.",
    "Buffet lines were long.",
])
def test_brand_and_ordinary_words_are_not_people(text):
    assert person_hits(text) == []


def test_brand_shield_does_not_hide_a_real_person_beside_it():
    assert person_hits("Merrill Lynch and Peter Lynch.") == ["peter lynch"]


def test_common_misspelling_of_a_denied_name_is_flagged():
    assert person_hits("Warren Buffet's rule is patience.")


def test_spaced_surname_variant_is_flagged():
    assert person_hits("Bankman Fried ran an exchange.")


@pytest.mark.parametrize("cp", [0x00AD, 0x034F, 0x180E, 0x2061, 0x2062, 0x2063, 0x2064, 0x061C],
                         ids=lambda cp: f"U+{cp:04X}")
def test_invisible_format_character_cannot_hide_a_name(cp):
    assert scan(f"Buf{chr(cp)}fett said so.") != []


_PATIENCE_CTX_SENTENCES = ("Patience matters for long-term owners of a business.",)
_PATIENCE_VOCAB = frozenset({"patience", "matters", "for", "owners", "of", "a", "business"})


@pytest.mark.parametrize("surname", ["Graham", "Lynch"])
def test_app_store_surname_alone_is_rejected_by_the_scan_on_its_own(surname):
    """One validator per test: combined, either guard alone satisfied `!= []`, so deleting
    either one survived (mutants C1 and G1 of the e1 review)."""
    text = clean(f"{surname} taught patience.")
    assert ("person_named", surname) in [(v.code, v.detail) for v in scan_text("f", text)]


@pytest.mark.parametrize("surname", ["Graham", "Lynch"])
def test_app_store_surname_alone_is_rejected_by_grounding_on_its_own(surname):
    ctx = build_context(_PATIENCE_CTX_SENTENCES, _PATIENCE_VOCAB)
    text = clean(f"{surname} taught patience.")
    assert ("person_named", surname) in [(v.code, v.detail)
                                         for v in check_grounding("f", text, ctx)]


#: Every branch of `_ambiguous_surname_hits`, called on scan_text ALONE (grounding would mask a
#: deleted guard for Graham/Lynch/Wood/Marks). For Cook, Jobs, Gates, Holmes, Chang and Buffet
#: this function is the ONLY guard: grounding reads them as ordinary English.
_SURNAME_SHAPES = (
    "Apple changed course under {S}.",          # mid-sentence capital
    "{S}'s plan worked.",                       # possessive
    "The plan was {S}' idea.",                  # bare-apostrophe possessive
    "Mr. {S} said so.",                         # honorific
    "T. {S} said so.",                          # initial
    "{S} kept the focus.",                      # sentence-initial + name verb
    "{S} pivoted the company.",                 # sentence-initial + -ed verb
    "A {S}-style plan.",                        # adjective suffix
)


@pytest.mark.parametrize("shape", _SURNAME_SHAPES)
@pytest.mark.parametrize("surname", c.AMBIGUOUS_SURNAMES)
def test_every_ambiguous_surname_is_caught_by_the_scan_alone(surname, shape):
    text = shape.format(S=surname)
    hits = person_hits(text)
    assert any(surname in h for h in hits), (text, hits)


@pytest.mark.parametrize("surname", [s for s in c.AMBIGUOUS_SURNAMES if s not in c._NOUN_SURNAMES])
def test_an_auxiliary_after_a_non_noun_surname_is_the_person(surname):
    assert person_hits(f"{surname} was patient.")


@pytest.mark.parametrize("text", ["Jobs are scarce.", "Wood is renewable.", "Marks were high.",
                                  "Gates are open.", "Cook is a verb.", "A wood-fired oven.",
                                  "The cook-off was fun."])
def test_an_auxiliary_after_a_noun_surname_is_the_word(text):
    assert person_hits(text) == []


# ── famous quotes ────────────────────────────────────────────────────────────


def _quotes():
    data = json.loads((DATA / "weekly_investor_quotes.json").read_text(encoding="utf-8"))
    return [q["text"] for q in data["quotes"]]


@pytest.mark.parametrize("quote", [q for q in _quotes()
                                   if len(re.findall(r"[a-z0-9']+", fold(q).replace("'", ""))) >= 6])
def test_every_vendored_quote_is_detected_verbatim(quote):
    assert "famous_quote" in codes(quote)


def test_a_six_word_window_inside_a_quote_is_detected_with_straight_quotes():
    # Week 1 (Graham) uses a curly apostrophe; a draft typing it straight must still match.
    assert "famous_quote" in codes("Remember: the investors chief problem and even more.")
    assert "famous_quote" in codes("Be fearful when others are greedy, they say.")


def test_a_paraphrase_is_not_a_famous_quote():
    assert "famous_quote" not in codes("Fear and greed move markets in cycles.")


# ── class B: value / price opinions ─────────────────────────────────────────

#: Opinion rows — must fire in BOTH strict (Money Moves) and relaxed (Journey) modes.
OPINION_SAMPLES = (
    "The company looked undervalued.",
    "It was over-valued.",
    "Its fair value is higher.",
    "Analysts set a price target.",
    "It became the most valuable company.",
    "It is a good buy.",
    "It is worth owning.",
    "Buy the dip.",
    "Sell the stock.",
    "Buy its shares.",
    "Investors should buy.",
    "Should you buy?",
    "It is time to sell.",
    "Consider buying index funds.",
    "You may want to buy.",
    "Buy when others panic.",
    "It earned a strong buy rating.",
    "It was a better bet.",
    "A smarter investment.",
    "Winners for investors.",
    "It will soar.",
    "It is poised to grow.",
    "It is set to double.",
    "It could triple.",
    "A ten-bagger.",
    "A multibagger.",
    "10x returns.",
    "To the moon.",
    # content-B review (idx 3, 9): a verdict noun, any N-bagger.
    "The stock is a buy.",
    "A 4-bagger.",
)

#: Neutral valuation vocabulary — rejected only with strict_instruments=True.
STRICT_ONLY_SAMPLES = (
    "Its share price moved.",
    "Earnings per share grew.",
    "Its market cap grew.",
    "Its valuation grew.",
    "It was valued at a premium.",
    "It traded near two dollars.",
    "The news was priced in.",
    "The P/E ratio measures price.",
    "Assets exceeded its market price.",
    "It was overpriced.",
    "Cheapness is not quality.",
    "Its intrinsic value grew.",
    "Look at what the market was paying.",
    "It was mispriced.",
    "Beware the value trap.",
    "Demand a margin of safety.",
    "Book value rose.",
    "A sum-of-the-parts view.",
    "A value opportunity.",
    "The stock fell 50%.",
    "Shares plunged after the report.",
    # content-B review (idx 3, 5): re-rating, willingness to pay, the market's reaction, the
    # investment verdict nouns, the pronoun verdict and "worth" + an amount.
    "Services quietly re-rated the whole company.",
    "It earned a higher multiple.",
    "Investors were willing to pay more for it.",
    "Wall Street punished the thin margins.",
    "It can reward patient owners for decades.",
    "It is a great investment.",
    "It is in a bubble.",
    "It is overhyped.",
    "It is priced for perfection.",
    "It is a core holding.",
    "Early owners grew rich.",
    "It deserves its premium.",
    "It was worth $7 billion.",
    "It is expensive.",
    "That price looks steep.",
    "A share costs $1.50.",
    # idx 8: digit-free market caps and price levels — valuation FACTS, so strict (a Journey
    # sentence naming a company gets them via `_sentence_hits`).
    "It became a trillion-dollar company.",
    "It became the world's largest company.",
    "It hit a 52-week high.",
    # Round 2 (W2CB-4/5): a named company's yield, something lifting the stock, the widened
    # market-cap nouns, a digit-free return, and "its run is not over".
    "It offers a steady dividend yield.",
    "Strong renewals lifted the shares for years.",
    "It became the most valuable chipmaker.",
    "It grew into one of the largest companies in history.",
    "It was one of the best performers of the decade.",
    "A small early bet turned into a fortune.",
    "Its best years may still be ahead.",
    # Round 2 (W2CB-12): "buyers" only when what they pay for is the instrument.
    "Buyers paid up for the shares.",
    # Round 3 (W3CB-6): a holder's windfall, holders who did well, a market's biggest winners,
    # and the move said by ellipsis.
    "Anyone who held it early made a fortune.",
    "Long-term holders have done extremely well.",
    "It was one of the decade's biggest winners.",
    "Sales hit a record, and so did the stock.",
)

#: Plan tier 1 ("looks/is cheap, bargain") — see the relaxed-mode BUG test below.
CHEAP_OPINION_SAMPLES = (
    "Its shares look cheap.",
    "The stock looks cheap.",
    "The company looked cheap on paper.",
    "The shares are a bargain.",
    # content-B review (idx 11/12): an adverb in between, and no determiner at all.
    "Its stock still looks cheap.",
    "Target shares are a bargain.",
)


def _class_b(text, **kw):
    return [v for v in scan(text, **kw) if v.code.startswith("class_b_")]


@pytest.mark.parametrize("text", OPINION_SAMPLES)
@pytest.mark.parametrize("strict", [True, False])
def test_opinion_rows_fire_in_both_modes(text, strict):
    assert _class_b(text, strict_instruments=strict), text


@pytest.mark.parametrize("text", STRICT_ONLY_SAMPLES)
def test_valuation_vocabulary_fires_only_in_strict_mode(text):
    assert _class_b(text, strict_instruments=True), text
    assert _class_b(text, strict_instruments=False) == [], text


@pytest.mark.parametrize("text", [
    "Shares fell 3% after the report.",
    "The stock hit a 52-week high.",
    "It became a trillion-dollar company.",
    "It is now the world's largest company by market value.",
    "Its market cap passed $3 trillion.",
])
def test_the_non_strict_rows_carry_no_valuation_fact(text):
    """`theme_insights_service` reuses the NON-strict rows for in-app summaries, where a price
    move or a market cap is exactly what may be said (the FMP licence covers the authenticated
    app). A valuation FACT row added there as non-strict would reject those summaries."""
    folded = fold(clean(text))
    hits = [p for _code, p, strict in c.CLASS_B_TIER1 if not strict and re.search(p, folded)]
    assert hits == [], (text, hits)


def test_strict_is_the_default():
    assert _class_b("Its market cap grew.")


@pytest.mark.parametrize("text", CHEAP_OPINION_SAMPLES)
def test_cheap_and_bargain_opinions_fire_in_relaxed_mode_too(text):
    assert _class_b(text, strict_instruments=True)
    assert _class_b(text, strict_instruments=False)


def test_every_class_b_row_is_exercised_by_a_sample():
    samples = [fold(clean(s)) for s in OPINION_SAMPLES + STRICT_ONLY_SAMPLES + CHEAP_OPINION_SAMPLES]
    uncovered = [p for _code, p, _strict in c.CLASS_B_TIER1
                 if not any(re.search(p, s) for s in samples)]
    assert uncovered == []


@pytest.mark.parametrize("text", [
    "The stock market crashed in 1929.",
    "Stocks fell in 2008.",
    "Owning shares means owning part of a business.",
    "A stock is a share of a company.",
    "Buyers and sellers set prices.",
    "The art of selling is persuasion.",
    "Investors buy and hold index funds.",
    "Prices will change over time.",
    "It traded near-term profit for position.",
    "Dollar-cost averaging is a strategy.",
])
@pytest.mark.parametrize("strict", [True, False])
def test_education_is_not_class_b(text, strict):
    assert _class_b(text, strict_instruments=strict) == [], text


# ── class B tier 2: evaluative words in an instrument / company context ─────


@pytest.mark.parametrize("text", [
    "Investors found it cheap.",
    "Investors found it inexpensive.",
    "The stock is cheap.",
    "Shareholders saw a bargain.",
    "The market offered a discount.",
])
def test_tier2_word_with_instrument_context_is_flagged(text):
    assert "class_b_evaluative" in codes(text)


@pytest.mark.parametrize("text", [
    # Not "Costco sells cheap goods." any more: Costco is in the company lexicon
    # (data/known_companies_en.txt), so it is a named issuer with or without the item's terms —
    # see test_tier2_word_next_to_a_company_term_is_flagged.
    "The warehouse sells cheap goods.",
    "Cheap goods drew shoppers.",
    "Shoppers found bargains in bulk.",
])
def test_tier2_word_in_a_retail_sentence_is_clean(text):
    assert codes(text) == []


def test_tier2_word_next_to_a_company_term_is_flagged():
    terms = frozenset({"costco"})
    assert "class_b_evaluative" in codes("Costco sells cheap goods.", company_terms=terms)
    assert "class_b_evaluative" in codes("Costco's goods are cheap.", company_terms=terms)
    assert codes("Costco sells in bulk.", company_terms=terms) == []


def test_tier2_company_term_scope_is_the_sentence():
    terms = frozenset({"costco"})
    assert codes("Costco sells in bulk. Cheap goods drew shoppers.", company_terms=terms) == []


def test_hyphenated_company_term_is_matched():
    assert "class_b_evaluative" in codes("Coca-Cola was a bargain.",
                                         company_terms=frozenset({"coca-cola"}))


# ── return figures ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "The fund returned 12% a year.",
    "It gained 40 percent.",
    "An annualized 7% gain.",
    "It compounded at 20 per cent.",
])
def test_percent_in_a_returns_context_is_flagged(text):
    assert "return_figure" in codes(text)


@pytest.mark.parametrize("text", [
    "Prices rise 3 percent a year.",
    "Inflation runs 3% a year.",
    "About 60% of profit came from the cloud.",
    "Returns matter. Fees are 1%.",
])
def test_percent_outside_a_returns_context_is_clean(text):
    assert "return_figure" not in codes(text)


# ── banned phrases, misattributions, identity, brand ────────────────────────


@pytest.mark.parametrize("phrase", c.BANNED_PHRASES)
def test_every_banned_phrase_is_detected(phrase):
    assert "banned_phrase" in codes(f"They said {phrase} again.")


@pytest.mark.parametrize("text", [
    "Talk to a financial advisor.",
    "Talk to a financial adviser.",
    "Ask an investment advisor.",
    "Ask an investment adviser.",
    "Guaranteed returns.",
    "Guaranteed profits await.",
    "It is risk-free.",
    "The #1 stock.",
    "The number one rule.",
])
def test_banned_samples(text):
    assert "banned_phrase" in codes(text)


@pytest.mark.parametrize("text", ["Nothing is guaranteed.", "It is guaranteed.",
                                  "Risk is part of investing."])
def test_guarantee_language_without_a_promise_is_clean(text):
    assert "banned_phrase" not in codes(text)


def test_misattributions_are_a_superset_of_the_learn_content_list():
    mod = _load_misattribution_module()
    learn = {fold(p) for p in mod.BANNED_PHRASES}
    assert learn, "the Learn misattribution list should not be empty"
    assert learn <= {fold(p) for p in c.MISATTRIBUTIONS}


@pytest.mark.parametrize("phrase", c.MISATTRIBUTIONS)
def test_every_misattribution_is_detected(phrase):
    assert "misattribution" in codes(f"People say {phrase} all the time.")


@pytest.mark.parametrize("term", c.IDENTITY_TERMS)
def test_every_identity_term_is_detected(term):
    assert "identity_leak" in codes(f"Made with {term} today.")


@pytest.mark.parametrize("text", ["Gemini wrote this.", "ChatGPT says hi.", "Powered by GPT-4.",
                                  "Claude helped.", "Anthropic built it.", "An LLM wrote it.",
                                  "OpenAI models.", "As an AI, the answer is no."])
def test_identity_samples(text):
    assert "identity_leak" in codes(text)


@pytest.mark.parametrize("term", c.BRAND_TERMS)
def test_every_brand_term_is_detected(term):
    assert "brand_mention" in codes(f"Try {term} today.")


@pytest.mark.parametrize("text", ["Link in bio.", "Download it now.", "Caydex explains.",
                                  "Cay AI explains.", "Get our app."])
def test_brand_and_cta_samples(text):
    assert "brand_mention" in codes(text)


# ── first person ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["I made 300% in a year.", "Let me show you.", "I've learned.",
                                  "Trust me on this.", "This is my rule."])
def test_declarative_first_person_is_flagged(text):
    assert "first_person" in codes(text)


@pytest.mark.parametrize("text", [
    "Ask yourself: am I giving it time?",
    'She thought, "I will wait."',
    "The reader asks: 'am I being patient' before acting.",
])
def test_questions_and_quoted_thoughts_are_exempt(text):
    assert "first_person" not in codes(text)


@pytest.mark.parametrize("text", ["My portfolio tripled.", "Mine grew faster.",
                                  "Myself included, everyone panicked."])
def test_sentence_initial_first_person_is_flagged(text):
    assert "first_person" in codes(text)


@pytest.mark.parametrize("text", [
    "Want to know how I stopped panic selling?",
    "Guess what my biggest investing mistake was?",
    "Remember when I sold everything at the bottom?",
    "Why did I buy at the top and sell at the bottom?",
    "Can you guess what I did next?",
])
def test_a_narrated_experience_in_question_form_is_a_testimonial(text):
    """The question exemption is for the READER's self-question, not a narrator's story told as
    a hook ("Want to know how I…?"), which a synthetic voice would speak as a testimonial."""
    assert "first_person" in codes(text)


@pytest.mark.parametrize("text", [
    "Ask yourself: am I giving it time?",
    "Ask yourself, would I still want this stock if the market closed?",
    "ask: do I understand this risk?",
    "Am I sizing this for what I know, or for what I only hope?",
    "Ask yourself: what do I actually own?",
    "Should I sell because the price fell?",
])
def test_the_readers_own_self_question_stays_exempt(text):
    assert "first_person" not in codes(text)


@pytest.mark.parametrize("text", ["Here is what we learned from Mr. Market.",
                                  "Our readers say this lesson changed everything.",
                                  "We have taught this to thousands of beginners.",
                                  "That lesson is ours.", "We've seen this before.",
                                  "What can we learn from Costco?"])
def test_the_brand_we_is_first_person(text):
    assert "first_person" in codes(text)


@pytest.mark.parametrize("text", ["The future loves to surprise us.", "The US market fell.",
                                  "Stocks in the U.S. rose."])
def test_us_is_not_the_brand_we(text):
    assert "first_person" not in codes(text)


# ── links, handles, markup, hashtags, cashtags ──────────────────────────────


@pytest.mark.parametrize("text", [
    "See http://x.co now.",
    "https://caydexinvest.com/go/x",
    "Visit www.example.org today.",
    "amazon.com sells books.",
    "caydexinvest dot com",
    "caydexinvest[.]com",
    "caydexinvest(dot)com",
    "javascript:alert(1)",
    "mailto:a",
    "data:text/html,hi",
])
def test_links_are_flagged(text):
    assert "link" in codes(text)


def test_handle_is_flagged_but_an_email_is_a_link_not_a_handle():
    assert "handle" in codes("Ask @someone today.")
    got = codes("Email a@b.co today.")
    assert "link" in got and "handle" not in got


@pytest.mark.parametrize("text", ["**bold**", "__init__", "<b>bold</b>", "</div>", "[x](y)",
                                  "![img](y)", "`code`"])
def test_markup_is_flagged(text):
    assert "markup" in codes(text)


def test_hashtag_is_flagged():
    assert "hashtag" in codes("Learn more #investing")


@pytest.mark.parametrize("text", ["$AAPL rallied.", "$ AAPL rallied.", "$aapl rallied.",
                                  "Watch ($TSLA) closely."])
def test_cashtags_are_flagged(text):
    assert "cashtag" in codes(text)


@pytest.mark.parametrize("text", ["$47B in sales.", "$47bn in sales.", "A $5 fee.", "$1.2K saved.",
                                  "US$5 fee."])
def test_currency_amounts_are_not_cashtags(text):
    assert "cashtag" not in codes(text)


# ── characters ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["Great \U0001F680", "Done \u2705"])
def test_emoji_only_when_not_allowed(text):
    assert "emoji" in codes(text)
    assert "emoji" not in codes(text, allow_emoji=True)


@pytest.mark.parametrize("text, letter", [("\u0410pple", "\u0410"), ("\u0391pple", "\u0391"),
                                          ("\u65e5\u672c", "\u65e5")])
def test_non_latin_letters_are_flagged(text, letter):
    got = [v for v in scan(text) if v.code == "non_latin"]
    assert got and letter in got[0].detail


def test_accented_latin_is_not_non_latin():
    assert "non_latin" not in codes("Caf\u00e9 na\u00efve r\u00e9sum\u00e9.")


def test_non_ascii_digits_are_flagged():
    assert "non_ascii_digit" in codes("Profit rose \u0663 points.")      # Arabic-Indic, survives NFKC
    # Uncleaned fullwidth digits (the scan itself must not rely on the caller's clean()).
    assert "non_ascii_digit" in [v.code for v in scan_text("f", "Price \uff14\uff17 today")]


# ── empty, too long, totality ───────────────────────────────────────────────


@pytest.mark.parametrize("text", ["", "   ", "\n\t", None])
def test_empty(text):
    assert [v.code for v in scan_text("f", text)] == ["empty"]  # type: ignore[arg-type]


def test_too_long_is_reported_and_the_prefix_is_still_scanned():
    text = "Buffett said so. " + "a " * 4000
    got = [v.code for v in scan_text("f", text)]
    assert got[0] == "too_long"
    assert "person_named" in got


def test_custom_max_chars():
    got = [v.code for v in scan_text("f", "Plain words only here.", max_chars=10)]
    assert got == ["too_long"]


def test_detail_is_bounded_and_field_is_propagated():
    vs = scan_text("captions.x", "The fund returned 12% a year " + "and more " * 60 + ".")
    assert vs and all(v.field == "captions.x" and len(v.detail) <= 160 for v in vs)


def test_scan_many_prefixes_each_field():
    vs = c.scan_many([("hook", "Buffett."), ("x", "Fine words.")])
    assert [(v.field, v.code) for v in vs] == [("hook", "person_named")]


# ── performance (ReDoS guard) ────────────────────────────────────────────────

_DEGENERATE = {
    "letters": "a" * 100_000,
    "digits": "9" * 100_000,
    "digit_commas": "1," * 50_000,
    "stock": "stock " * 16_667,
    "shares": "shares " * 14_286,
    "stock_then_move": "stock " * 16_000 + "fell",
    "trade": "trade " * 16_667,
    "buffett": "buffett " * 12_500,
    "percent": "1% " * 33_333,
    "dollar": "$" * 100_000,
    "hash": "#" * 100_000,
    "at": "@a" * 50_000,
    "dots": "." * 100_000,
    "domains": "a." * 50_000,
    "hyphens": "a-" * 50_000,
    "angles": "<" * 100_000,
    "stars": "*" * 100_000,
    "double_quotes": '"' * 100_000,
    "single_quotes": "'a " * 33_333,
    "newlines": "a\n" * 50_000,
    "spaces": " " * 99_999 + "a",
    "emoji": "\U0001F680" * 100_000,
    "cyrillic": "\u0410" * 100_000,
    "zero_width": "a\u200b" * 50_000,
    # The content-A rules: first names, name pairs, roles, promises, CTAs, emphasis, domains.
    "given_names": "Warren " * 14_286,
    "noun_names_mid": "the Bill " * 11_111,
    "name_pairs": "Walt Disney " * 8_334,
    "capitals": "Aaa " * 25_000,
    "man_who": "the man who taught " * 5_264,
    "safe_way": "safe way to " * 8_334,
    "emphasis": "*a " * 33_333,
    "tildes": "~a ~" * 25_000,
    "domain_runs": "a.bc" * 25_000,
    "self_questions": "am I? " * 16_667,
    "we": "we " * 33_333,
    "guarantees": "guarantees your " * 6_250,
    "negated_risk": "no " * 33_333 + "risk",
    "possessive_roles": "Apple's " * 12_500,
    "quote_roots": "greedy others fearful " * 4_546,
    "entities": "&amp;" * 20_000,
    "accents": "\u00e9" * 100_000,
    "interpuncts": "a\u00b7" * 50_000,
    "ideographic_dots": "a\u3002" * 50_000,
    # Round 2 (bypass fix): clause scope, governing frames, affirming negations, word-brands
    # in price sentences, role position, subject-bound records, vouched-for content, company
    # placement / value rows, quoted questions and thoughts.
    "neg_commas": "don't panic, " * 7_700 + "always go up",
    "governing": "never assume that " * 5_900,
    "affirming": "not a myth " * 9_100,
    "brand_verdict": "Apple cheap " * 8_400,
    "role_position": "its leader " * 9_100,
    "record_nouns": "sales hit record highs " * 4_400,
    "vouched": "readers love this rule " * 4_400,
    "co_rows": "Costco belongs in every " * 4_200,
    "quoted_questions": '"what? ' * 14_300,
    "quoted_thoughts": 'thought, "a ' * 9_100,
}


def _warm():
    scan_text("f", "Warm up Buffett $AAPL 5% returns http://x.co.")


@pytest.mark.parametrize("name", sorted(_DEGENERATE))
def test_scan_is_fast_on_a_100k_degenerate_input(name):
    _warm()
    text = _DEGENERATE[name]
    best = float("inf")
    for _ in range(2):
        t0 = time.perf_counter()
        out = scan_text("f", text)
        best = min(best, time.perf_counter() - t0)
    assert isinstance(out, list)
    assert best < 0.25, f"{name}: {best:.3f}s"


def test_the_scan_cap_holds_even_when_max_chars_is_larger():
    _warm()
    t0 = time.perf_counter()
    scan_text("f", "1," * 6_000, max_chars=10**6)
    assert time.perf_counter() - t0 < 0.25


# ── people pointed at without a lexicon name (content-A review, idx 0/2/16) ─


#: Epithets that point at one real person. In EVERY mode: Journey's Mr. Market lesson is where a
#: writer reaches for "the father of value investing".
DESCRIBED_PEOPLE = (
    "A legendary value investor made this idea famous.",
    "A well-known investor made this parable famous.",
    "The greatest investor alive keeps this parable close.",
    "The father of value investing created Mr. Market.",
    "The godfather of index investing built a fund.",
    "The Oracle invented this parable.",
    "The Sage liked this parable.",
    "A famous value investor invented Mr. Market.",
    "A billionaire value investor loves this parable.",
    "A well-known billionaire once said to be patient.",
    "One of the most successful investors of all time advised patience.",
    "Mr. Market was invented by the man who taught value investing.",
    "It was coined by a famous professor.",
    "An investing legend kept it simple.",
    "The Fed chair raised rates.",
    "Apple's CEO kept the focus on services.",
    "Tesla's own CEO called it production hell.",
    "Microsoft's new chief executive changed course.",
    "Amazon's founder was famously patient about profit.",
    "Nvidia's leather-jacketed CEO saw this coming.",
    "LVMH's chairman set the pattern.",
)


@pytest.mark.parametrize("text", DESCRIBED_PEOPLE)
@pytest.mark.parametrize("strict", [True, False])
def test_a_person_described_by_epithet_or_a_named_companys_role_is_a_person(text, strict):
    assert "person_named" in codes(text, strict_instruments=strict), text


#: A singular role or he/she in a Money Moves case study is one real person (strict mode only).
MONEY_MOVES_PEOPLE = (
    "In 2001, a CEO famously called Linux a cancer.",
    "Its CEO once called Linux a cancer.",
    "A new CEO turned Microsoft toward the cloud.",
    "Its founder still controls the vote.",
    "The chairman set the pattern.",
    "The chair of the board resigned.",
    "One man has run LVMH for three decades.",
    "The model was run by essentially one person for three decades.",
    "The man who built it kept control.",
    "What he actually bought.",
    "The pattern she set has lasted.",
    "The company's president changed the plan.",
    "Tesla nearly failed during what its own CEO called 'production hell'.",
)


@pytest.mark.parametrize("text", MONEY_MOVES_PEOPLE)
def test_a_singular_role_or_pronoun_is_a_person_in_a_money_moves_post(text):
    assert "person_named" in codes(text, strict_instruments=True), text


@pytest.mark.parametrize("text", [
    "So his moods become your opportunity, not your boss.",   # Journey: the fictional Mr. Market
    "You are never forced to trade with him.",
    "When the executives who know the company best are quietly cashing out, ask why.",
    "Read the CEO's letter to shareholders before you buy.",
    "A CEO who quietly sells shares is a signal worth a second look.",
    "Mr. Market's boss is your own plan.",
])
def test_journey_keeps_generic_roles_and_the_fictional_he(text):
    assert "person_named" not in codes(text, strict_instruments=False), text


@pytest.mark.parametrize("text", [
    "The company's executives cut costs.",
    "Founders often keep voting control.",
    "Its founder-led culture shaped decisions.",
    "Management matters more than any single product.",
    "CEOs of large companies are paid in shares.",
    "No one person can predict the market.",
    "The greatest investors stay humble.",
    "Famous investors disagree about this.",
    "A famous brand keeps customers.",
    "A legendary product line.",
    "Fund managers charge fees.",
    "A famous one, the S&P five hundred, holds big companies.",
    "Compete with the Oracle database.",
    "The legendary Kirkland icon returned.",
])
@pytest.mark.parametrize("strict", [True, False])
def test_plural_and_generic_roles_are_not_a_person(text, strict):
    assert "person_named" not in codes(text, strict_instruments=strict), text


#: A listed person's FIRST name used as a name ("Uncle Warren", "Ben invented Mr. Market").
FIRST_NAME_PEOPLE = (
    "Uncle Warren's favourite habit is patience with Mr. Market.",
    "Uncle Warren would call this patience.",
    "As Warren likes to say, be patient.",
    "Warren would call this patience.",
    "Peter would say know your companies.",
    "Ben invented Mr. Market to teach patience.",
    "Benjamin invented Mr. Market to teach patience.",
    "Warren's favourite habit is patience.",
    "A Warren-style habit.",
    "Mark renamed Facebook to Meta Platforms.",
    "Jeff built Amazon into a flywheel.",
    "Charlie hated debt.",
    "Why Warren waits for a fat pitch.",
)


@pytest.mark.parametrize("text", FIRST_NAME_PEOPLE)
def test_a_listed_persons_first_name_used_as_a_name_is_a_person(text):
    assert person_hits(text), text


#: A founder's first name + surname doing what only a PERSON does, or after an age/kinship
#: word — even where the sheet names the brand ("Louis Vuitton" is the LVMH house).
@pytest.mark.parametrize("text", [
    "Henry Ford said customers could pick any colour.",
    "Walt Disney founded a studio.",
    "Young Louis Vuitton made trunks.",
    "Uncle Walt Disney built franchises.",
    "Christian Dior believed in a small line.",
])
def test_a_first_name_and_surname_doing_what_a_person_does_is_a_person(text):
    assert person_hits(text), text


@pytest.mark.parametrize("text", [
    "Mark your calendar.", "Bill payments rose.", "Will prices rise?", "Pat yourself on the back.",
    "Pay the Bill First", "The Deutsche Mark was replaced.", "The Union Jack flew.",
    "Max Markup Cap: ~15%.", "Sam's Club competes on price.", "Ben & Jerry's sells ice cream.",
    "Louis Vuitton began making trunks in 1854.", "Louis Vuitton merged with Moet Hennessy.",
    "Charles Schwab accounts hold cash.", "Walt Disney Company owns studios.",
    "J.P. Morgan lends money.", "Morgan Stanley advises companies.",
])
def test_first_names_as_words_or_brands_are_not_people(text):
    assert person_hits(text) == [], text


def test_the_given_name_list_is_vendored_and_non_trivial():
    names = c.given_names()
    assert len(names) >= 500
    # Founders a model volunteers about the corpus's companies.
    for n in ("walt", "henry", "christian", "louis", "charles", "jeff", "mark", "warren", "sam"):
        assert n in names, n
    # Modal verbs, months and headline words never sit in a name slot.
    for n in ("will", "may", "june", "april", "august", "max", "price", "grant", "chase"):
        assert n not in names, n


def test_denied_first_names_come_from_the_person_lexicon():
    denied = c.denied_given_names()
    for n in ("warren", "peter", "ben", "benjamin", "charlie", "cathie", "ray", "jeff", "mark"):
        assert n in denied, n
    assert all(len(n) >= 3 and n.isalpha() for n in denied)


# ── quotations (idx 14) ──────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "Be greedy when others are fearful, and fearful when others are greedy.",   # swapped halves
    "When the tide goes out, you see who was swimming naked.",                  # paraphrase
    "Only when the tide goes out do you learn who has been swimming naked.",
    "Price is what you pay. Value is what you get.",
    "Short term, the market votes. Long term, it weighs.",
    "Our favorite holding period is forever.",
    "Risk comes from not knowing what you are doing.",
    "As one legend put it, be patient.",
    "An old investing proverb says be patient.",
    "As the saying goes, patience pays.",
    "In the words of a great teacher, stay calm.",
    "A famous investor once said to stay calm.",
])
def test_famous_sayings_and_attribution_frames_are_rejected(text):
    assert "famous_quote" in codes(text), text


def test_the_clause_check_is_order_insensitive_against_the_vendored_quotes():
    """No signature phrase and no shared contiguous 6-gram: only the clause-overlap check can
    see that these are the vendored week-21 (Housel) and week-12 (Lynch) lines."""
    for text in ("The highest dividend money pays is controlling your own time.",
                 "Corrections themselves lose investors less money than preparing for them."):
        assert c._ngram_quote_hit(fold(text)) == "", text
        assert c._clause_overlap_hit(fold(text)), text
        assert "famous_quote" in codes(text), text


@pytest.mark.parametrize("text", [
    "The best opportunities often feel uncomfortable, bought when others are fearful, not when "
    "everyone is cheering.",                      # journey:fomo_cycle's own fact sentence
    "Cash flow is a famous warning sign when it shrinks.",
    "Do nothing.", "Time is your friend.", "Buy good companies.",
    "Fear and greed move markets in cycles.",
    "Getting people to put it on every day has been the harder problem.",
])
def test_ordinary_teaching_is_not_a_famous_quote(text):
    assert "famous_quote" not in codes(text), text


# ── promises and the disclaimer's subject (idx 1) ────────────────────────────


PROMISES = (
    "Over the long run, the market always goes up.",
    "Stocks always go up in the long run.",
    "The market always recovers, so never sell in a panic.",
    "Historically, the market has always recovered from every crash.",
    "Markets always bounce back for investors who stay patient.",
    "Time in the market always beats timing the market.",
    "Patient investors never lose money.",
    "Patient shareholders never lose money.",
    "You won't lose money if you wait.",
    "Index funds never go down for long.",
    "Compounding guarantees your money grows.",
    "Compounding guarantees your savings will grow.",
    "The business model guarantees growth.",
    "Guaranteed: patience pays.",
    "Growth is almost guaranteed.",
    "Investing involves no risk if you are patient.",
    "Investing involves no real risk if you hold long enough.",
    "Index funds are a safe way to grow your money.",
    "ETFs are a safe, simple way to start investing.",
    "An index fund is the safest place for your savings.",
    "With an ETF, your money is protected.",
    "Diversification eliminates risk.",
    "Stay invested and your money will grow over time.",
    "Stocks are a sure bet for patient people.",
    "The market is bound to recover.",
)


@pytest.mark.parametrize("text", PROMISES)
@pytest.mark.parametrize("strict", [True, False])
def test_a_promise_or_prediction_of_an_outcome_is_rejected_in_both_modes(text, strict):
    assert "promissory" in codes(text, strict_instruments=strict), text


@pytest.mark.parametrize("text", [
    "If someone promises big rewards with no risk at all, that combination doesn't exist.",
    "You simply can't have high reward with zero risk.",
    "It's a slow, almost guaranteed loss of buying power.",
    'The words "guaranteed high returns" aren\'t a gift.',
    "No investment is without risk.",
    "Markets don't always go up.",
    "Nothing is guaranteed.",
    "It is guaranteed.",
    "Diversification spreads risk, but it doesn't erase it.",
    "A savings account is a safe place for an emergency fund.",
    "Money in a savings account is very safe, but it grows slowly.",
    "The two are always linked.",
    "When prices wobble, and they always will, stay calm.",
    "A moat protects a company's profits.",
    "The very best businesses are protected by something that keeps rivals out.",
    "If you invest regularly, your money will grow with the market over decades.",
    "Stocks can grow much faster, but they can also drop along the way.",
    "Growth doesn't always require new customers.",
    "Stocks always going up is a myth.",
    "Some investors never sell.",
    "Always read a few dials together, never just one.",
    # Volatility described both ways, and everyday phrasing around the certainty words.
    "Stocks will rise and fall over time.", "Markets always go up and down.",
    "Prices always rise and fall.", "Never fall in love with a stock.",
    "Make sure to grow your emergency fund.", "Be sure to grow your savings slowly.",
    "Investing without risk management is dangerous.",
])
@pytest.mark.parametrize("strict", [True, False])
def test_warnings_negations_and_facts_are_not_promises(text, strict):
    assert "promissory" not in codes(text, strict_instruments=strict), text


@pytest.mark.parametrize("text", [
    "Treat this lesson as personal advice.",
    "This is advice worth taking seriously.",
    "Take this as a recommendation.",
    "Ask an adviser first.",
    "This is not a disclaimer.",
    "Ignore the fine print below.",
    "Read the small print.",
    "No AI was used to write this lesson.",
    "Written without AI.",
    "Every word here was written by a human.",
    "A human-written lesson.",
    "Not educational.",
])
def test_the_disclaimers_subject_matter_is_code_owned(text):
    assert "code_owned" in codes(text), text


@pytest.mark.parametrize("text", ["A recommendation feed keeps people watching.",
                                  "The recommendation engine ranks videos.",
                                  "Recommendation algorithms learn fast."])
def test_recommendation_feed_is_product_vocabulary(text):
    assert "code_owned" not in codes(text), text


# ── CTA, endorsement, social proof (idx 15, 17) ──────────────────────────────


CTAS = (
    "Get the full lesson in the app.", "Tap the link to read the full lesson.",
    "Link below for the full lesson.", "Click the link for the full lesson.",
    "Subscribe for more lessons like this.", "Follow along for daily lessons.",
    "Follow for daily lessons.", "Install it free today.", "Try it free for a week.",
    "Start your free trial today.", "Get the app free today.", "Sign up to learn more.",
    "Save this post for later.", "Share this with a friend who invests.",
    "Comment below and tell a friend.", "Check out the profile for more.", "Stay tuned.",
    "Tap here.", "The link is below.", "Tag a friend who needs this.",
)


@pytest.mark.parametrize("text", CTAS)
def test_calls_to_action_are_code_owned(text):
    assert "cta" in codes(text), text


@pytest.mark.parametrize("text", [
    "Follow the money.", "Follow the Cash.", "Then follow the plan, not the panic.",
    "Many ETFs simply follow a list called an index.",
    "Buy that single ETF, and in one click you own a slice of all of them.",
    "The easy reaction is baked in before you even tap the buy button.",
    "Every time you tap a card, a sliver flows to the network.",
    "A challenger would have to sign up billions of cardholders.",
    "Some products get more useful as more people join.",
    "Apple's installed base passed two billion devices.",
    "Netflix built a subscriber lead.", "The battle isn't subscribers.",
    "Users stay in the app for hours.", "People open the app with no plan.",
    "Save money every month.", "The link between risk and reward is real.",
    "Lessons like this one repeat.", "Follow the plan today.",
    "Members who join today pay a fee.", "Customers subscribe to Netflix for its shows.",
])
def test_corpus_vocabulary_is_not_a_cta(text):
    assert "cta" not in codes(text), text


@pytest.mark.parametrize("text", [
    "Endorsed by the SEC.", "SEC-approved investing education.",
    "Reviewed by a certified financial planner.", "Thousands of learners already use this lesson.",
    "Rated the top investing lesson by users.", "Our readers say this lesson changed everything.",
    "Readers love this method.", "A top-rated lesson.", "Vetted by professionals.",
    "This lesson was reviewed by experts.", "Recommended by investors everywhere.",
])
def test_endorsements_and_social_proof_are_rejected(text):
    assert "endorsement" in codes(text), text


@pytest.mark.parametrize("text", [
    "Orders measured in thousands of aircraft.", "Millions of merchants accept cards.",
    "It is trusted by all of its customers.", "A state-backed rival entered.",
    "Graduate students who learned it first.", "Billions of users scroll daily.",
    "The fund is backed by government bonds.",
    "Members love the treasure hunt.", "Viewers love big franchises.",
    # Case-study facts: an approval by a non-finance authority is not an endorsement of a post.
    "The drug was approved by regulators.", "Each jet must be certified by regulators.",
    "Approved by the FAA, the jet returned to service.",
])
def test_corpus_counts_and_trust_are_not_social_proof(text):
    assert "endorsement" not in codes(text), text


# ── links, markup, entities (idx 13, 46) ─────────────────────────────────────


@pytest.mark.parametrize("text", [
    "Free investor education lives at investor.gov.",
    "Filings live on sec.gov for anyone to read.",
    "SEC.gov keeps the filings.",
    "Treasury bonds can be bought at treasurydirect.gov.",
    "Free lessons at investor.gov/introduction today.",
    "Read more at caydexinvest.page today.",
    "See harvard.edu for the study.",
    "Visit goo.gl slash abc.",
    "It is at t.co now.",
    "Read more at caydexinvest。com today.",       # IDEOGRAPHIC FULL STOP
    "Read more at caydexinvest｡com today.",       # HALFWIDTH (NFKC → U+3002)
    "Visit caydexinvest·com for more.",           # MIDDLE DOT between lower-case letters
    "Visit caydexinvest‧com for more.",           # HYPHENATION POINT
    "Read more at caydexinvést.com.",             # accented label (IDN)
    "Read more at example&period;com today.",          # entity-encoded dot
    "Visit caydexinvest . com today.",                 # spaced dot
    "Visit caydexinvest dot gov today.",
    "Visit x.y.com today.", "See vs.com for more.",     # abbreviation-shaped, but a real TLD
])
def test_every_written_domain_is_a_link(text):
    assert "link" in codes(text), text


@pytest.mark.parametrize("text", [
    "e.g. a fund.", "i.e. the cost.", "The U.S. market fell.", "Mr. Market is moody.",
    "Visa vs. Mastercard.", "Revenue was 3.5x higher.", "Annual Services Revenue: ~$85B+.",
    "It was 2.0 in total.", "Marvel·Star Wars", "The dot-com bubble burst.",
    "Each dot is a data point.", "A dot in the chart.", "It ended at 5 p.m. sharp.",
    # A missing space after an abbreviation is a typo, not a host.
    "Prices in U.S.dollars rose.", "Costs, e.g.the rent, rose.", "Visa vs.the rest.",
])
def test_abbreviations_decimals_and_separators_are_not_links(text):
    assert "link" not in codes(text), text


@pytest.mark.parametrize("text", ["Mr. Market is *moody*.", "Mr. Market is _moody_.",
                                  "Mr. Market ~~never~~ always.", "# Mr. Market",
                                  "> Mr. Market is moody.", "Mr. Market ||moody|| you.",
                                  "Mr. Market &lt;b&gt;moody&lt;/b&gt;.",
                                  "Mr. Market &amp;lt;b&amp;gt; you."])
def test_more_markup_is_flagged(text):
    assert "markup" in codes(text), text


@pytest.mark.parametrize("text", ["S&P 500 holds large companies.", "R&D costs money.",
                                  "Scale & Logistics matter.", "Services Gross Margin: ~70%.",
                                  "It grew from ~20 to ~70% of sales.", "Use snake_case names.",
                                  "5*3 is fifteen.", "1. Start small.", "- Keep costs low.",
                                  "A footnote*", "#1 is not markup either"])
def test_plain_text_symbols_are_not_markup(text):
    assert "markup" not in codes(text), text


def test_clean_decodes_entities_once_and_keeps_literal_ampersands():
    assert clean("Mr. Market &amp; you.") == "Mr. Market & you."
    assert clean("S&P 500 & R&D") == "S&P 500 & R&D"
    assert clean("Buf&#8203;fett") == "Buffett"                 # decoded, THEN stripped
    assert clean("example&period;com") == "example.com"
    assert clean("&amp;lt;b&amp;gt;") == "&lt;b&gt;"           # one decode; markup rejects it


# ── accents and look-alike letters (idx 18) ──────────────────────────────────


@pytest.mark.parametrize("text, code", [
    ("Warren Buffétt loved Mr. Market.", "person_named"),
    ("Warren Buffétt loved Mr. Market.", "person_named"),   # combining acute
    ("Peter Lýnch loved Mr. Market.", "person_named"),
    ("Büffett loved this parable.", "person_named"),
    ("an idea buffétt loved.", "person_named"),
    ("Written with Gémini.", "identity_leak"),
    ("Written with Gemıni.", "identity_leak"),                # dotless i
    ("Cáydex has the full lesson.", "brand_mention"),
    ("#WarrenBüffett", "person_named"),
    ("Buf·fett said so.", "person_named"),                   # inner interpunct
])
def test_an_accent_does_not_hide_a_lexicon_word(text, code):
    assert code in codes(text), text


def test_skeleton_keeps_case_and_folds_only_for_matching():
    assert c.skeleton("Moët Hennessy décor") == "Moet Hennessy decor"
    assert c.skeleton("Buff‐ett") == "Buff-ett"
    assert c.fold("CAFÉ İstanbul") == "cafe istanbul"
    assert clean("Moët") == "Moët"                        # the stored text is unchanged
    assert codes("Moët Hennessy and home décor.") == []


def test_non_latin_letters_are_still_flagged_after_the_skeleton():
    assert "non_latin" in codes("Аpple")


# ── lexicons pinned against FROZEN, independent lists (idx 31) ───────────────
#
# The tests above that parametrise over `c.UNAMBIGUOUS_SURNAMES` etc. lose their case when an
# entry is deleted. These lists are written HERE, never read from the module, so deleting
# "bezos", "musk", "passive income" or "follow us" from compliance.py fails a test.

_APP_STORE_LISTING = BACKEND.parent / "documents" / "legal" / "app-store-listing.md"


def _listing_section(start: str, stop: str) -> str:
    text = _APP_STORE_LISTING.read_text(encoding="utf-8")
    a = text.index(start)
    return text[a:text.index(stop, a)]


def _listing_do_not_use_names():
    section = _listing_section("## \u26d4 Do not use", "and any other named individual")
    return [n.strip() for n in re.findall(r"\*\*([^*]+)\*\*", section)[0].split("·")]


def test_the_app_store_listing_is_parsed_non_trivially():
    names = _listing_do_not_use_names()
    assert len(names) >= 12 and "Warren Buffett" in names and "Robert Kiyosaki" in names


@pytest.mark.parametrize("name", _listing_do_not_use_names())
def test_every_app_store_do_not_use_name_is_flagged(name):
    assert person_hits(f"{name} said so."), name
    surname = name.split()[-1]
    assert person_hits(f"As {surname} said, be patient."), surname


def _listing_also_avoid():
    section = _listing_section("\nAlso avoid:", "---")
    return re.findall(r'"([^"]+)"', section)


@pytest.mark.parametrize("phrase", _listing_also_avoid())
def test_every_app_store_also_avoid_phrase_is_rejected(phrase):
    # "guaranteed" is deliberately narrowed to a PROMISE ("It is guaranteed." stays clean), so
    # the listing's bare word is checked in its promissory shape. Round 2: "signals" likewise
    # to the PRODUCT noun ("our signals", "buy signals", "signals to buy") — the verb in "Growth
    # in revenue and earnings signals expansion" (a real draft) is not the listing's word.
    text = ("Growth is guaranteed." if phrase == "guaranteed"
            else "They sell buy signals again." if phrase == "signals"
            else f"They said {phrase} again.")
    assert scan(text), phrase


@pytest.mark.parametrize("text", [
    "They sell buy signals.", "Get our signals every morning.", "Premium signals for members.",
    "Watch for signals to buy.", "Real-time trading signals.", "The app sends AI signals.",
])
def test_signals_as_a_product_are_banned(text):
    assert "banned_phrase" in {v.code for v in scan(text)}, text


@pytest.mark.parametrize("text", [
    "Growth in revenue and earnings signals expansion.",
    "This transition signals a maturation of the streaming market.",
    "Some signals don't shout.",
    "Warning signals are easy to miss.",
])
def test_signals_as_a_verb_or_a_plain_noun_pass(text):
    for strict in (True, False):
        assert scan(text, strict_instruments=strict) == [], (text, strict)


#: Written by hand. The executives the raw corpus names or a model volunteers about those
#: companies, the investors, and the vendors — each must be caught by BEHAVIOUR (a scan hit),
#: not by tuple membership, so moving an English-word surname to the ambiguous list stays legal.
_FROZEN_PEOPLE = (
    "Bezos", "Jeff Bezos", "Jassy", "Nadella", "Satya Nadella", "Ballmer", "Arnault",
    "Bernard Arnault", "Morris Chang", "Lisa Su", "Huang", "Jensen Huang", "Musk", "Elon Musk",
    "Zuckerberg", "Mark Zuckerberg", "Tim Cook", "Steve Jobs", "Bill Gates", "Sinegal",
    "Jim Sinegal", "Reed Hastings", "Bob Iger", "Pichai", "Buffett", "Munger", "Dalio", "Burry",
    "Ackman", "Greenblatt", "Housel", "Kiyosaki", "Bogle", "Templeton", "Keynes", "Klarman",
    "Soros", "Icahn", "Pelosi", "Dimon", "Blankfein",
)
_FROZEN_VENDORS = ("Gemini", "Google DeepMind", "DeepMind", "Bard", "Vertex AI", "OpenAI",
                   "ChatGPT", "GPT-4", "Claude", "Anthropic", "a large language model")
_FROZEN_CTA = ("Follow us for more.", "Follow for more lessons.", "Link in bio.",
               "Download it now.", "Find it on the App Store.", "Get our app.")
_FROZEN_HYPE = ("Passive income is the real goal here.", "Get rich slowly.",
                "Financial freedom starts here.", "Top picks for the year.",
                "Back up the truck.", "An insider tip.", "Even a superinvestor keeps it simple.")


@pytest.mark.parametrize("name", _FROZEN_PEOPLE)
def test_frozen_people_are_flagged(name):
    assert person_hits(f"{name} said so."), name


@pytest.mark.parametrize("vendor", _FROZEN_VENDORS)
def test_frozen_vendors_are_flagged(vendor):
    assert "identity_leak" in codes(f"Made with {vendor} today."), vendor


@pytest.mark.parametrize("text", _FROZEN_CTA + _FROZEN_HYPE)
def test_frozen_cta_and_hype_phrases_are_flagged(text):
    assert scan(text), text


# ── legitimate educational copy must still pass (false-positive guard) ───────


MUST_PASS = (
    "Costco sells in bulk.", "Index funds spread risk across many companies.",
    "The company's executives cut costs.", "Founders often keep voting control.",
    "Diversification spreads risk, but it doesn't erase it.", "No investment is without risk.",
    "Stocks can fall as well as rise.", "Markets don't always go up.", "Nothing is guaranteed.",
    "A savings account is a safe place for an emergency fund.",
    "The market has always had bad years.", "Costco always keeps prices low.",
    "A moat protects a company's profits.", "Management matters.",
    "Ask yourself: do I understand this business?", "Follow the money.",
    "Tap a card and a fee flows to the network.", "Netflix has more subscribers.",
    "Users stay in the app for hours.", "Save money every month.",
    "The future loves to surprise us.", "Mark your calendar.", "Bill payments rose.",
    "Will prices rise?", "Graham crackers are cheap snacks.", "Merrill Lynch is a brokerage.",
    "Wood prices rose.", "Jobs are scarce.", "The link between risk and reward is real.",
    "The S&P 500 holds large companies.", "Services Gross Margin: ~70%.",
    "e.g. a fund.", "The U.S. market fell.", "Mr. Market is moody.", "Visa vs. Mastercard.",
    "Recommendation feeds keep people watching.",
    "Louis Vuitton began making trunks in 1854.", "Moët Hennessy merged with Louis Vuitton.",
    "Home décor sells well.", "Max Markup Cap: ~15%.", "Rival Disney built franchises.",
    "Why Disney Won the Franchise Game.", "Thousands of aircraft sit in the backlog.",
    "Time is your friend.", "Do nothing.", "The two are always linked.",
    "If someone promises big rewards with no risk at all, that combination doesn't exist.",
    "You simply can't have high reward with zero risk.",
    "Growth doesn't always require new customers.", "Bulk packs lower the cost per unit.",
    "The dot-com bubble burst.", "A famous brand keeps customers.",
)


@pytest.mark.parametrize("text", MUST_PASS)
def test_legitimate_educational_copy_passes_in_both_modes(text):
    assert codes(text, strict_instruments=True) == [], text
    assert codes(text, strict_instruments=False) == [], text


# ── suitability verdicts on an instrument, in BOTH modes (overblock-fixer handoff) ──


@pytest.mark.parametrize("text", [
    "The S&P 500 is a safe bet on America.",
    "Index funds are a sure bet.",
    "An index fund is a good choice for most people.",
    "ETFs are a solid pick for beginners.",
])
@pytest.mark.parametrize("strict", [False, True])
def test_a_suitability_verdict_on_an_instrument_is_a_recommendation(text, strict):
    codes = {v.code for v in scan_text("x", text, strict_instruments=strict)}
    assert "class_b_recommendation" in codes, (text, strict, codes)


@pytest.mark.parametrize("text", [
    "Index funds spread risk across many companies.",
    "Bonds are generally safer than stocks.",
    "ETFs are a simple way to start learning how funds work.",
])
def test_describing_an_instrument_is_not_a_suitability_verdict(text):
    assert scan_text("x", text, strict_instruments=False) == []


# ── no frame licenses a promise about a named company (round-3 frames handoff) ──
# A myth label, a debunking answer or a warning frame exempts "stocks always go up" — a
# misconception about markets. The same shape with a company in it still talks about that
# company's share price in public (EU MAR), so it is rejected whatever frames it.


@pytest.mark.parametrize("text", [
    "Myth: Apple stock always goes up.",
    "Does Apple stock always go up? No.",
    "Many think Costco shares can never fall. That's a myth.",
    "Beware of claims that Nvidia stock is risk-free.",
])
@pytest.mark.parametrize("strict", [False, True])
def test_a_framed_promise_about_a_named_company_is_still_rejected(text, strict):
    codes = {v.code for v in scan_text("x", text, strict_instruments=strict)}
    assert "promissory" in codes or "banned_phrase" in codes, (text, strict, codes)


@pytest.mark.parametrize("text", [
    "Myth: stocks always go up. Fact: they fall too.",
    "Many think ETFs are without risk. That's a myth.",
    "Does the market always go up? No.",
])
def test_the_same_frames_still_exempt_a_misconception_about_markets(text):
    assert scan_text("x", text, strict_instruments=False) == []
