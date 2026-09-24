"""
Adversarial tests for the marketing writer's number parser and grounding check
(`app/services/marketing/numbers.py`, `app/services/marketing/grounding.py`).

Written against what the module docstrings and the Phase 2 plan PROMISE, not against what the
code happens to do: every number in a public post must be one its fact sheet states (value +
unit class + an anchor word from the same source sentence), and every capitalised token must be
ordinary English or named in that fact sheet. A test marked `# BUG:` asserts the promised
behaviour and fails today; it is kept failing on purpose until the source is fixed.

Category 1 (pure): no network, no Supabase. The grounding context is built from a small
hand-written fact sheet through the real `build_context`, so the only data file touched is the
bundled web2 dictionary the entity rule reads.
"""

from __future__ import annotations

import time

import pytest

from app.services.marketing import grounding as g
from app.services.marketing.compliance import Violation, clean, scan_text
from app.services.marketing.numbers import (
    CURRENCY,
    MULTIPLE,
    PERCENT,
    PLAIN,
    YEAR,
    NumberMention,
    extract_numbers,
    has_non_ascii_digit,
    same_number,
    spelled_numbers,
    words_to_digits,
)

# ── fixtures ─────────────────────────────────────────────────────────────────

#: A hand-written fact sheet. Each sentence carries one number whose ANCHORS are its content
#: words; the tests below reuse the same value in the right and in the wrong context.
FACTS = (
    "About 60% of operating profit came from the cloud division.",
    "Costco charges members an annual fee of $65.",
    "The company opened its first warehouse in 1983.",
    "By 2020 the chain ran 800 warehouses.",
    "Membership renewal rates stayed near 90 percent.",
    "Its sales grew forty-seven billion dollars over the decade.",
    "LVMH owns many luxury brands.",
    "One million members joined the club.",
    "The firm filed a 10-K and a Schedule 13D.",
)

#: A deliberately tiny "corpus vocabulary" (words seen in lower case). Ordinary English beyond
#: it must come from the bundled dictionary, which is what production relies on too.
VOCAB = frozenset(
    "the a of about operating profit came from cloud division charges members annual fee "
    "company opened its first warehouse by chain ran warehouses membership renewal rates "
    "stayed near sales grew over decade owns many luxury brands one million joined club "
    "firm filed and schedule".split()
)


@pytest.fixture(scope="module")
def ctx() -> g.GroundingContext:
    return g.build_context(FACTS, VOCAB)


def codes(violations):
    return [v.code for v in violations]


def ground(ctx, text):
    return [(v.code, v.detail) for v in g.check_grounding("f", text, ctx)]


def mentions(text):
    return [(m.value, m.unit) for m in extract_numbers(text)]


# ── numbers.words_to_digits ──────────────────────────────────────────────────


@pytest.mark.parametrize("text, expected", [
    ("forty-seven billion dollars", "47000000000 dollars"),
    ("Twenty-Five members", "25 members"),
    ("ninety-nine", "99"),
    ("a dozen eggs", "12 eggs"),
    ("A dozen eggs", "12 eggs"),
    ("one hundred and five", "105"),
    ("a hundred and fifty thousand", "150000"),
    ("seven hundred and fifty-two billion", "752000000000"),
    ("a million users", "1000000 users"),
    ("one million two hundred thousand", "1200000"),
    ("three hundred thousand", "300000"),
    ("zero", "0"),
    ("one, two, three", "1, 2, 3"),
])
def test_words_to_digits_converts_cardinals(text, expected):
    assert words_to_digits(text) == expected


@pytest.mark.parametrize("text", [
    "It raised 200 million dollars.",
    "A $40 billion deal.",
    "Sales hit 3 thousand units.",
    "Nearly 12 dozen stores.",
    "About 5 hundred people.",
])
def test_bare_scale_word_after_digits_is_not_a_second_number(text):
    # "200 million" converted alone became "200 1000000": two ungrounded numbers where the
    # source had one. The digit parser owns the scale suffix.
    assert words_to_digits(text) == text
    assert len(extract_numbers(words_to_digits(text))) == 1


def test_bare_scale_suffix_is_read_by_the_digit_parser():
    assert mentions(words_to_digits("It raised 200 million dollars.")) == [(2e8, CURRENCY)]
    assert mentions(words_to_digits("A $40 billion deal.")) == [(4e10, CURRENCY)]


@pytest.mark.parametrize("text", [
    "first", "second", "the third rule", "in the twenty-first century",
    "a one-time fee", "two-thirds of revenue", "a nine-to-five job", "a two-step plan",
    "an hour", "a lot", "half of it", "millions of users", "hundreds of stores",
    "several million", "a few hundred", "",
])
def test_ordinals_fractions_and_compounds_are_untouched(text):
    assert words_to_digits(text) == text


def test_words_to_digits_tolerates_none():
    assert words_to_digits(None) == ""  # type: ignore[arg-type]
    assert spelled_numbers(None) == []  # type: ignore[arg-type]


def test_adjacent_units_are_not_summed_into_a_new_number():
    assert "6" not in words_to_digits("one two three")
    assert "40" not in words_to_digits("in twenty twenty")


def test_half_a_million_is_not_read_as_one_million(ctx):
    assert ground(ctx, "Half a million members joined the club.") != []


def test_a_huge_spelled_number_does_not_raise():
    text = "one " + "hundred " * 2200
    words_to_digits(text)


def test_spelled_scale_run_is_linear_time():
    words_to_digits("warm up")
    text = "hundred " * 3000  # 24,000 chars: ~1 s today, ~10 ms if linear
    t0 = time.perf_counter()
    words_to_digits(text)
    assert time.perf_counter() - t0 < 0.25


# ── numbers.extract_numbers ──────────────────────────────────────────────────


@pytest.mark.parametrize("text, expected", [
    ("1,200", [(1200.0, PLAIN)]),
    ("1200", [(1200.0, PLAIN)]),
    ("1.2k", [(1200.0, PLAIN)]),
    ("$1.2K", [(1200.0, CURRENCY)]),
    ("$47B", [(47e9, CURRENCY)]),
    ("$47bn", [(47e9, CURRENCY)]),
    ("$1.2 billion", [(1.2e9, CURRENCY)]),
    ("47 billion dollars", [(47e9, CURRENCY)]),
    ("US$5", [(5.0, CURRENCY)]),
    ("\u20ac5", [(5.0, CURRENCY)]),
    ("\u00a33.5m", [(3.5e6, CURRENCY)]),
    ("$1,200,000", [(1.2e6, CURRENCY)]),
    ("~29%", [(29.0, PERCENT)]),
    ("29 percent", [(29.0, PERCENT)]),
    ("29 per cent", [(29.0, PERCENT)]),
    ("3 percentage points", [(3.0, PERCENT)]),
    ("0.5%", [(0.5, PERCENT)]),
    ("10x", [(10.0, MULTIPLE)]),
    ("12.5x", [(12.5, MULTIPLE)]),
    ("3 times", [(3.0, MULTIPLE)]),
    ("2006", [(2006.0, YEAR)]),
    ("the 1990s", [(1990.0, YEAR)]),
    ("2101", [(2101.0, PLAIN)]),
    ("1799", [(1799.0, PLAIN)]),
    ("2010-2020", [(2010.0, YEAR), (2020.0, YEAR)]),
    ("2010\u20132020", [(2010.0, YEAR), (2020.0, YEAR)]),
    ("2010\u21922020", [(2010.0, YEAR), (2020.0, YEAR)]),
    ("$1.2B \u2192 $3B", [(1.2e9, CURRENCY), (3e9, CURRENCY)]),
    ("$90 \u2192 ~$0", [(90.0, CURRENCY), (0.0, CURRENCY)]),
    ("200M+", [(2e8, PLAIN)]),
    ("10,000,000", [(1e7, PLAIN)]),
    ("", []),
])
def test_extract_numbers_formats(text, expected):
    assert mentions(text) == expected


def test_decade_is_flagged_and_equals_its_year():
    (m,) = extract_numbers("the 1990s")
    assert m.decade is True and m.unit == YEAR and m.value == 1990
    (y,) = extract_numbers("in 1990")
    assert same_number(m, y)


def test_unicode_minus_and_ascii_minus_parse_the_same():
    assert mentions("\u22125%") == mentions("-5%") == [(5.0, PERCENT)]


@pytest.mark.parametrize("text", [
    "S&P 500", "S&P500", "401(k)", "401 (k)", "13F", "13D", "10-K", "10-Q", "5G", "3D",
    "Web3", "B2B", "Q3", "24/7", "Form 4",
])
def test_name_numbers_are_not_quantities(text):
    assert extract_numbers(text) == []


def test_a_real_number_next_to_a_name_number_is_still_extracted():
    assert mentions("The S&P 500 fell 20% in 2008.") == [(20.0, PERCENT), (2008.0, YEAR)]
    assert mentions("A 401(k) match of 5%.") == [(5.0, PERCENT)]
    assert mentions("5G phones sold 300 million units.") == [(3e8, PLAIN)]


def test_extract_numbers_offsets_point_at_the_raw_text():
    text = "Fees fell to $65 in 1983."
    for m in extract_numbers(text):
        assert m.raw in text[m.start:m.end]


# ── numbers.has_non_ascii_digit ──────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "\uff14\uff17",        # fullwidth ４７
    "\u0663",              # Arabic-Indic ٣
    "\u06f5",              # extended Arabic-Indic ۵
    "\u0967",              # Devanagari १
    "\u00bd",              # vulgar fraction ½
    "\u00b2",              # superscript ²
    "\u2082",              # subscript ₂
    "\u2460",              # circled ①
    "\u2167",              # Roman numeral Ⅷ
    "price \uff14\uff17 today",
])
def test_non_ascii_digits_are_detected(text):
    assert has_non_ascii_digit(text) is True


@pytest.mark.parametrize("text", ["", "0123456789", "$47B up 5%", "caf\u00e9 na\u00efve", "\u2014 \u2019"])
def test_ascii_digits_and_ordinary_unicode_are_not_flagged(text):
    assert has_non_ascii_digit(text) is False


def test_has_non_ascii_digit_tolerates_none():
    assert has_non_ascii_digit(None) is False  # type: ignore[arg-type]


# ── numbers.same_number ──────────────────────────────────────────────────────


def _m(value, unit):
    return NumberMention(value=value, unit=unit, raw=str(value), start=0, end=1)


@pytest.mark.parametrize("a, b, same", [
    ((29, PERCENT), (29, PERCENT), True),
    ((29, PERCENT), (29, PLAIN), False),
    ((65, CURRENCY), (65, PLAIN), False),
    ((65, CURRENCY), (65, PERCENT), False),
    ((3, MULTIPLE), (3, PLAIN), False),
    ((3, MULTIPLE), (3, CURRENCY), False),
    ((2014, YEAR), (2014, PLAIN), True),       # "in 2014" vs the source's "2014 launch"
    ((2014, PLAIN), (2014, YEAR), True),
    ((2014, YEAR), (2015, YEAR), False),
    ((1200, PLAIN), (1200.0000000001, PLAIN), True),
    ((0.1 + 0.2, PERCENT), (0.3, PERCENT), True),
    ((60, PERCENT), (6, PERCENT), False),
    ((47e9, CURRENCY), (47e6, CURRENCY), False),
])
def test_same_number_unit_strictness(a, b, same):
    assert same_number(_m(*a), _m(*b)) is same
    assert same_number(_m(*b), _m(*a)) is same


def test_formats_that_mean_the_same_quantity_compare_equal():
    (a,) = extract_numbers("1,200")
    (b,) = extract_numbers("1.2k")
    (c,) = extract_numbers("$1.2K")
    assert same_number(a, b)
    assert not same_number(a, c)
    (p,) = extract_numbers("~29%")
    (q,) = extract_numbers("29 percent")
    assert same_number(p, q)
    (x,) = extract_numbers("3x")
    (t,) = extract_numbers("3 times")
    assert same_number(x, t)


# ── grounding: numbers ───────────────────────────────────────────────────────


def test_fact_sheet_numbers_are_extracted_with_anchors(ctx):
    by_value = {(f.value, f.unit): f for f in ctx.numbers}
    assert (60.0, PERCENT) in by_value
    assert {"cloud", "profit"} <= by_value[(60.0, PERCENT)].anchors
    # The spelled-out source number is converted before extraction.
    assert (47e9, CURRENCY) in by_value
    assert (1e6, PLAIN) in by_value


def test_empty_text_has_no_violations(ctx):
    assert g.check_grounding("f", "", ctx) == []
    assert g.check_grounding("f", None, ctx) == []  # type: ignore[arg-type]


def test_violations_carry_the_field_name(ctx):
    vs = g.check_grounding("captions.x", "It rose 70%.", ctx)
    assert vs and all(isinstance(v, Violation) and v.field == "captions.x" for v in vs)


@pytest.mark.parametrize("text, detail", [
    ("It rose 70%.", "70%"),
    ("The fee was $66.", "$66"),
    ("The fee was $65k.", "$65k"),
    ("It ran 801 warehouses.", "801"),
    ("The first warehouse opened in the 1980s.", "1980s"),
    ("Sales grew $48B over the decade.", "$48B"),
])
def test_ungrounded_number_is_flagged(ctx, text, detail):
    assert ("ungrounded_number", detail) in ground(ctx, text)


@pytest.mark.parametrize("text", [
    "About 60% of operating profit came from the cloud.",
    "Cloud work drove 60% of profit.",
    "Operating profit: 60%.",
    "Members paid an annual fee of $65.",
    "The annual fee was 65 dollars.",
    "In 1983 the company opened its first warehouse.",
    "It had 800 warehouses by 2020.",
    "Renewal rates stayed near 90%.",
    "Renewal rates stayed near 90 percent.",
    "Sales grew $47B over the decade.",
    "Sales grew 47 billion dollars.",
    "1 million members joined.",
])
def test_same_number_in_the_right_context_passes(ctx, text):
    assert ground(ctx, text) == []


@pytest.mark.parametrize("text, detail", [
    ("The stock rose 60%.", "60%"),
    ("It fell 60% in a year.", "60%"),
    ("The stock fell in 2020.", "2020"),
    ("Debt rose to 1 million.", "1 million"),
])
def test_matching_number_in_the_wrong_context_is_an_anchor_miss(ctx, text, detail):
    # Same value, same unit, no shared anchor: the source's "60% of operating profit"
    # cannot reappear as a stock move.
    assert ("number_context", detail) in ground(ctx, text)


@pytest.mark.parametrize("text", [
    "The stock rose 90 percent.",
    "The stock rose 47 billion dollars.",
])
def test_the_unit_word_is_not_an_anchor(ctx, text):
    assert codes(g.check_grounding("f", text, ctx)) == ["number_context"]


@pytest.mark.parametrize("text", [
    "3 lessons from a warehouse chain.",
    "Five lessons.",
    "0 shortcuts.",
    "1 rule, 2 habits, 4 mistakes.",
    "Step 5 is patience.",
    "5 years of habits.",
])
def test_small_counts_are_structural_and_exempt(ctx, text):
    assert ground(ctx, text) == []


@pytest.mark.parametrize("text, detail", [
    ("Up 5% on the year.", "5%"),
    ("Costco charged $3 more.", "$3"),
    ("A 3x jump.", "3x"),
    ("It grew 3 times.", "3 times"),
    ("It won 2 billion users.", "2 billion"),
    ("It bought 4 shares.", "4"),
    ("A 2.5 point gain.", "2.5"),
    ("Only 6 lessons.", "6"),
])
def test_quantities_are_not_structural(ctx, text, detail):
    assert ("ungrounded_number", detail) in ground(ctx, text)


@pytest.mark.parametrize("raw", [
    pytest.param("3/4 of operating profit came from the cloud.", id="ascii_3/4"),
    pytest.param("\u00be of operating profit came from the cloud.", id="vulgar_fraction"),
])
def test_a_fraction_is_a_quantity_not_a_count(ctx, raw):
    text = clean(raw)
    combined = scan_text("f", text) + g.check_grounding("f", text, ctx)
    assert combined != []


@pytest.mark.parametrize("text, detail", [
    ("Twelve members paid the annual fee.", "12"),
    ("Eleven lessons.", "11"),
    ("Sales grew forty-eight billion dollars.", "48000000000 dollars"),
    ("Sixty percent of the stock rose.", "60 percent"),
])
def test_spelled_out_numbers_are_held_to_the_same_grounding(ctx, text, detail):
    got = ground(ctx, text)
    assert got and got[0][1] == detail


def test_spelled_out_number_in_the_right_context_passes(ctx):
    assert ground(ctx, "Sales grew forty-seven billion dollars over the decade.") == []
    assert ground(ctx, "Sixty percent of operating profit came from the cloud.") == []


def test_a_300_digit_number_does_not_raise(ctx):
    g.check_grounding("f", "It sold " + "9" * 320 + " units.", ctx)


def test_a_huge_spelled_number_does_not_raise_in_grounding(ctx):
    g.check_grounding("f", "one " + "hundred " * 2200, ctx)


# ── grounding: name-numbers ──────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "The S&P 500 index.", "A 401(k) plan.", "The 13F report.", "The firm filed a 10-K.",
    "The firm filed a 10-Q.", "Open 24/7.", "It filed a Schedule 13D.",
])
def test_generic_or_grounded_name_numbers_pass(ctx, text):
    assert ground(ctx, text) == []


@pytest.mark.parametrize("text, detail", [
    ("5G networks.", "5G"),
    ("Web3 wallets.", "Web3"),
    ("A PS5 console.", "PS5"),
])
def test_ungrounded_name_number_is_flagged(ctx, text, detail):
    assert ("ungrounded_name_number", detail) in ground(ctx, text)


def test_name_number_grounding_is_token_bounded(ctx):
    assert ("ungrounded_name_number", "3D") in ground(ctx, "3D printing grew.")


# ── grounding: entities ──────────────────────────────────────────────────────


@pytest.mark.parametrize("text, name", [
    ("Jassy doubled down.", "Jassy"),              # sentence-initial is NOT exempt
    ("The pivot Jassy made.", "Jassy"),
    ("Walmart members.", "Walmart"),
    ("Members left for Walmart.", "Walmart"),
    ("Nvidia chips.", "Nvidia"),
    ("#Walmart", "Walmart"),
    ("Walmart's fee.", "Walmart"),
    ("Tim Cook said so.", "Tim"),
])
def test_unknown_proper_noun_is_flagged(ctx, text, name):
    assert ("ungrounded_entity", name) in ground(ctx, text)


@pytest.mark.parametrize("word", ["Understanding", "Consistency", "Automating", "Compounding",
                                  "Fluctuate", "Affordable", "Stakeholders"])
def test_title_case_ordinary_words_are_not_entities(ctx, word):
    assert ground(ctx, f"{word} matters.") == []


def test_title_case_ordinary_compound_is_not_an_entity(ctx):
    assert ground(ctx, "Carmakers matter.") == []


@pytest.mark.parametrize("text", [
    "Costco members.", "Costco's fee.", "Costco-style fee.", "LVMH brands.",
    "The CEO and the ETF.", "CPUs and GPUs.", "ETFs and IPOs.", "AI and the US economy.",
    "In January, sales grew.", "American and European buyers.",
])
def test_grounded_or_ordinary_capitals_pass(ctx, text):
    assert ground(ctx, text) == []


_ALPHA_ACRONYMS = sorted(a for a in g.ACRONYMS if not any(c.isdigit() for c in a))
_DIGIT_ACRONYMS = sorted(a for a in g.ACRONYMS if any(c.isdigit() for c in a))


@pytest.mark.parametrize("acronym", _ALPHA_ACRONYMS)
def test_every_allowlisted_acronym_passes(ctx, acronym):
    assert ground(ctx, f"Think about {acronym} carefully.") == []


@pytest.mark.parametrize("acronym", _DIGIT_ACRONYMS)
def test_allowlisted_acronyms_with_a_digit_pass(ctx, acronym):
    assert ground(ctx, f"Think about {acronym} carefully.") == []


@pytest.mark.parametrize("ticker", ["NVDA", "AAPL", "TSLA", "BRK", "AMZN", "NEVER"])
def test_all_caps_token_needs_the_allowlist_or_the_fact_sheet(ctx, ticker):
    assert ("ungrounded_acronym", ticker) in ground(ctx, f"Think about {ticker} carefully.")


def test_plural_of_an_unknown_ticker_is_still_flagged(ctx):
    assert ("ungrounded_acronym", "NVDA") in ground(ctx, "Two NVDAs.")


def test_ticker_named_in_the_fact_sheet_is_accepted():
    ctx2 = g.build_context(("AAPL reported results.",), VOCAB)
    assert g.check_grounding("f", "AAPL is a ticker.", ctx2) == []


# ── grounding: people behind a brand, accents, regulators (content-A review) ─


def _sheet(*sentences):
    return g.build_context(sentences, VOCAB)


def _codes_details(ctx_, text):
    return [(v.code, v.detail) for v in g.check_grounding("f", clean(text), ctx_)]


@pytest.mark.parametrize("sheet, text, name", [
    (("Disney built parks and a studio.",), "Walt Disney built a studio.", "Walt Disney"),
    (("Ford built cars for a century.",), "Henry Ford would recognise the factories.", "Henry Ford"),
    (("Ford built cars for a century.",), "Henry Ford's assembly line still matters.", "Henry Ford"),
    (("Dior is a fashion house.",), "Christian Dior's house kept its designers.", "Christian Dior"),
    (("Amazon ships fast.",), "Jeff B. built Amazon.", "Jeff B"),
    (("Amazon ships fast.",), "Sam Walton built a rival.", "Sam Walton"),
    (("Patience matters.",), "Why Warren Waits", "Warren Waits"),
])
def test_a_first_name_in_front_of_a_name_the_sheet_never_pairs_is_a_person(sheet, text, name):
    """The sheet names the BRAND; the founder in front of it is a person the writer added."""
    assert ("person_named", name) in _codes_details(_sheet(*sheet), text)


@pytest.mark.parametrize("sheet, text", [
    (("Louis Vuitton began making trunks in 1854.",), "Louis Vuitton made luggage."),
    (("Louis Vuitton began making trunks in 1854.",), "Louis Vuitton's trunks lasted."),
    (("Disney built parks.",), "Rival Disney built parks."),
    (("Disney built parks.",), "Will Disney keep its parks?"),
    (("Disney built parks.",), "Why Disney Won"),
    (("Patience matters.",), "Mark Your Calendar"),
    (("Patience matters.",), "Pay the Bill First"),
    (("Costco caps its markup.",), "Max Markup Cap."),
])
def test_a_brand_the_sheet_states_or_an_ordinary_title_is_not_a_person(sheet, text):
    assert [c for c, _ in _codes_details(_sheet(*sheet), text) if c == "person_named"] == []


def test_graham_style_is_the_person_unless_the_sheet_uses_the_word():
    assert ("person_named", "Graham-style") in _codes_details(_sheet("Patience matters."),
                                                              "A Graham-style habit.")
    crackers = _sheet("Graham crackers are a snack.", "They sell graham crackers.")
    assert [c for c, _ in _codes_details(crackers, "Graham-style snacks.") if c == "person_named"] == []


def test_a_misspelled_denied_surname_is_a_person_too():
    assert "buffet" in g.DENIED_SURNAMES
    assert ("person_named", "Buffet-style") in _codes_details(_sheet("Patience matters."),
                                                              "A Buffet-style habit.")


def test_wood_is_a_person_only_where_the_sheet_never_writes_the_word():
    assert ("person_named", "Wood") in _codes_details(_sheet("Patience matters."),
                                                      "Wood made the call.")
    lumber = _sheet("Home centres sell wood and tools.")
    assert _codes_details(lumber, "Wood sells well.") == []


def test_a_camel_case_brand_is_never_a_person():
    """ "PayPal" is a brand's capitals: grounded when the sheet names it, otherwise an ungrounded
    ENTITY — never `person_named` (no given-name or surname rule may fire on it)."""
    named = _sheet("PayPal moved money online.")
    assert _codes_details(named, "PayPal grew fast.") == []
    unnamed = _codes_details(_sheet("Patience matters."), "PayPal grew fast.")
    assert ("ungrounded_entity", "PayPal") in unnamed
    assert all(code != "person_named" for code, _ in unnamed)


@pytest.mark.parametrize("text, expected", [
    ("B\u00fcffett loved this parable.", ("person_named", "Buffett")),
    ("Buff\u00e9tt loved this parable.", ("person_named", "Buffett")),
    ("Written with G\u00e9mini.", ("ungrounded_entity", "Gemini")),
    ("C\u00e1ydex explains.", ("ungrounded_entity", "Caydex")),
])
def test_an_accent_cannot_split_a_name_past_the_entity_check(text, expected):
    assert expected in _codes_details(_sheet("Patience matters."), text)


def test_an_accented_word_must_be_one_the_sheet_itself_uses():
    lvmh = _sheet("LVMH merged with Mo\u00ebt Hennessy.", "Home d\u00e9cor sells well.")
    assert _codes_details(lvmh, "Mo\u00ebt Hennessy joined LVMH.") == []
    assert _codes_details(lvmh, "Home d\u00e9cor sells.") == []
    # Word-level, not letter-level: "é" occurs in "décor", but "Buffétt" is not a sheet word.
    assert ("non_latin", "Buff\u00e9tt") in _codes_details(lvmh, "Buff\u00e9tt knew.")
    assert ("non_latin", "caf\u00e9") in _codes_details(lvmh, "A caf\u00e9 sells coffee.")


def test_a_u2010_hyphen_is_read_like_an_ascii_hyphen():
    assert _codes_details(_sheet("Owners think long-term."), "Long\u2010term owners win.") == []


def test_a_regulator_acronym_must_come_from_the_sheet():
    assert "SEC" not in g.ACRONYMS
    assert ("ungrounded_acronym", "SEC") in _codes_details(_sheet("Patience matters."),
                                                           "Endorsed by the SEC.")
    assert _codes_details(_sheet("The SEC reviews filings."), "The SEC reviews filings.") == []


# ── grounding: performance and totality ─────────────────────────────────────


_DEGENERATE = {
    "letters": "a" * 100_000,
    "title_case": "Aa " * 33_333,
    "caps": "AAAA " * 20_000,
    "digit_commas": "1," * 50_000,
    "stock": "stock " * 16_667,
    "currency": "$1 " * 33_333,
    "dots": "." * 100_000,
    "newlines": "a\n" * 50_000,
    "percent": "1% " * 33_333,
    "hyphens": "A-" * 50_000,
    "apostrophes": "a'" * 50_000,
    "ampersands": "A&" * 50_000,
    "one": "one " * 25_000,
    "article": "a " * 50_000,
    "multiples": "10x " * 25_000,
    "name_numbers": "Web3 " * 20_000,
    "name_pairs": "Walt Disney " * 8_334,
    "denied_pairs": "Warren Waits " * 7_693,
    "accented_words": "Caf\u00e9 " * 20_000,
    "accents_only": "\u00e9" * 100_000,
}


@pytest.mark.parametrize("name", sorted(_DEGENERATE))
def test_grounding_is_fast_and_total_on_degenerate_input(ctx, name):
    g.english_roots()
    g.check_grounding("f", "Warm Understanding up.", ctx)
    text = _DEGENERATE[name]
    best = float("inf")
    for _ in range(2):
        t0 = time.perf_counter()
        out = g.check_grounding("f", text, ctx)
        best = min(best, time.perf_counter() - t0)
    assert isinstance(out, list)
    assert best < 1.0, f"{name}: {best:.3f}s"
