"""
Numbers after the content-B review: the parser's lost units (idx 48), spelled and hyphenated
multiples (idx 9), numbers bound to their LOCAL context and refused as a price or worth claim
(idx 6), digit-free magnitudes (idx 8), and the regression cases the earlier passes owed
(fractions, "1/0", "a half million", "two and a half", "Form 4", the too_long and
grounding_error paths).

Category 1 (pure): no network, no Supabase.
"""

from __future__ import annotations

import logging

import pytest

from app.services.marketing import grounding as g
from app.services.marketing.compliance import clean, scan_text
from app.services.marketing.numbers import (
    CURRENCY,
    FOREIGN_CURRENCY,
    FRACTION,
    MULTIPLE,
    PERCENT,
    PLAIN,
    YEAR,
    extract_numbers,
    same_number,
    words_to_digits,
)


def mentions(text):
    return [(m.value, m.unit) for m in extract_numbers(words_to_digits(text))]


# ── idx 48: units the parser used to drop ────────────────────────────────────


@pytest.mark.parametrize("text, expected", [
    ("40 bps", [(0.4, PERCENT)]),
    ("400 basis points", [(4.0, PERCENT)]),
    ("50 basis point", [(0.5, PERCENT)]),
    ("¥40B", [(4e10, FOREIGN_CURRENCY)]),
    ("40 billion yen", [(4e10, FOREIGN_CURRENCY)]),
    ("40B yen", [(4e10, FOREIGN_CURRENCY)]),
    ("₹40", [(40.0, FOREIGN_CURRENCY)]),
    ("JPY 40", [(40.0, FOREIGN_CURRENCY)]),
    ("NT$40", [(40.0, FOREIGN_CURRENCY)]),
    ("HK$40", [(40.0, FOREIGN_CURRENCY)]),
    ("40 rupees", [(40.0, FOREIGN_CURRENCY)]),
    ("US$40", [(40.0, CURRENCY)]),
    ("$40", [(40.0, CURRENCY)]),
    (".7%", [(0.7, PERCENT)]),
    ("$.50", [(0.5, CURRENCY)]),
    ("15-20%", [(15.0, PERCENT), (20.0, PERCENT)]),
    ("15 to 20 percent", [(15.0, PERCENT), (20.0, PERCENT)]),
    ("15–20%", [(15.0, PERCENT), (20.0, PERCENT)]),
    ("2-3x", [(2.0, MULTIPLE), (3.0, MULTIPLE)]),
    ("$15-20", [(15.0, CURRENCY), (20.0, CURRENCY)]),
    ("20-50 bps", [(0.2, PERCENT), (0.5, PERCENT)]),
    ("2010-2015", [(2010.0, YEAR), (2015.0, YEAR)]),
])
def test_a_unit_is_never_lost(text, expected):
    got = mentions(text)
    assert [(round(v, 9), u) for v, u in got] == expected, (text, got)


@pytest.mark.parametrize("text", [
    "v2.5 launched",          # a blanked name-number must not leave a ".5" behind
    "The Model 3 won awards.",   # "won" is the verb, not the currency
])
def test_a_leading_dot_or_a_verb_is_not_a_unit(text):
    got = mentions(text)
    assert all(u == PLAIN for _v, u in got) and all(v != 0.5 for v, _u in got), (text, got)


def test_basis_points_ground_against_the_same_percentage():
    (bps,) = extract_numbers("50 bps")
    (pct,) = extract_numbers("0.5%")
    assert same_number(bps, pct)


def test_a_foreign_amount_never_equals_a_dollar_or_a_plain_number():
    (yen,) = extract_numbers("¥50")
    (usd,) = extract_numbers("$50")
    (plain,) = extract_numbers("50")
    assert not same_number(yen, usd) and not same_number(yen, plain)


_COSTCO = g.build_context(
    ("Costco caps most markups around 15 percent when other retailers routinely take 25, "
     "50, or more.", "Markups of 0.15% are rare."),
    frozenset("costco caps most markups around percent when other retailers routinely take or "
              "more of are rare".split()),
)


@pytest.mark.parametrize("text", [
    "Other retailers routinely take 50 basis points.",
    "Other retailers routinely take ¥50.",
    "Other retailers routinely take 50 yen.",
])
def test_the_reviews_unit_swaps_are_ungrounded(text):
    codes = [v.code for v in g.check_grounding("f", clean(text), _COSTCO)]
    assert "ungrounded_number" in codes, (text, codes)


def test_a_leading_dot_decimal_is_its_own_number():
    """".15%" used to parse as 15% and grounded on the source's "15 percent"."""
    assert g.check_grounding("f", "Markups of .15% are rare.", _COSTCO) == []
    only_15 = g.build_context(("Costco caps most markups around 15 percent.",),
                              frozenset("costco caps most markups around percent".split()))
    assert ("ungrounded_number", ".15%") in [(v.code, v.detail) for v in g.check_grounding(
        "f", "Costco caps most markups around .15%.", only_15)]


def test_a_ranges_lower_bound_takes_the_upper_bounds_unit():
    got = [(v.code, v.detail) for v in g.check_grounding(
        "f", "Other retailers routinely take 25-50%.", _COSTCO)]
    # The source's 25 and 50 are unit-less: neither end of a percent range may ground on them.
    assert ("ungrounded_number", "25") in got and ("ungrounded_number", "50%") in got, got


# ── idx 9: multiples ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, expected", [
    ("twentyfold", "20x"), ("Fivefold", "5x"), ("five-fold", "5x"), ("tenfold", "10x"),
    ("twenty-five-fold", "25x"), ("a hundredfold", "a 100x"), ("thousandfold", "1000x"),
    ("twofold", "2x"), ("a ten-bagger", "a 10-bagger"),
])
def test_a_spelled_multiple_becomes_its_digit_form(text, expected):
    assert words_to_digits(text) == expected


@pytest.mark.parametrize("word", ["manifold", "scaffold", "unfold", "billfold", "blindfold",
                                  "centerfold", "manyfold", "fold", "foldable"])
def test_a_word_ending_in_fold_is_not_a_multiple(word):
    assert words_to_digits(word) == word and mentions(word) == []


@pytest.mark.parametrize("text", ["5-fold", "5fold", "5 fold", "20-fold", "4-bagger",
                                  "10 baggers", "twentyfold", "five-fold", "a ten-bagger"])
def test_every_multiple_form_parses_as_a_multiple(text):
    got = mentions(text)
    assert len(got) == 1 and got[0][1] == MULTIPLE, (text, got)


_NVIDIA = g.build_context(("NVIDIA launched CUDA in 2006.", "Revenue rose 40% that year."),
                          frozenset("launched in revenue rose that year".split()))


@pytest.mark.parametrize("text, detail", [
    ("NVIDIA grew twentyfold.", "20x"),
    ("NVIDIA grew five-fold.", "5x"),
    ("NVIDIA rose 5-fold.", "5-fold"),
    ("NVIDIA was a 4-bagger for early owners.", "4-bagger"),
    ("NVIDIA revenue grew twentyfold.", "20x"),
])
def test_a_small_multiple_is_no_longer_an_exempt_count(text, detail):
    """"5-fold" and "4-bagger" used to parse as PLAIN 5 / 4 — exempt as small counts."""
    assert ("ungrounded_number", detail) in [(v.code, v.detail)
                                             for v in g.check_grounding("f", clean(text), _NVIDIA)]


def test_twofold_in_teaching_copy_is_a_deliberate_false_reject():
    """Decided, not accidental: "the problem is twofold" is an ungrounded MULTIPLE unless the
    sheet states it. One repair round rephrases it; exempting small multiples is how "5-fold"
    got through."""
    assert [v.code for v in g.check_grounding("f", "The problem is twofold.", _NVIDIA)] == [
        "ungrounded_number"]


# ── idx 6: a number is bound to its local context, and refused as a price ────

_SHEET = g.build_context(
    (
        "Services Gross Margin: ~70%.",
        "Renewal rates sit above ninety percent in its core markets.",
        "Paid for Mellanox: ~$6.9B.",
        "Reality Labs Losses, 2021-23: ~$40B.",
        "Across 2021, 2022 and 2023 the division reported roughly forty billion dollars of "
        "cumulative operating losses.",
        "Apple sold billions of devices.",
    ),
    frozenset("services gross margin renewal rates sit above in its core markets paid for "
              "losses across and the division reported roughly of cumulative operating sold "
              "billions devices".split()),
)


@pytest.mark.parametrize("text, detail", [
    ("As Services took off, Apple climbed 70%.", "70%"),
    ("Apple rallied 90% as renewal rates held.", "90%"),
    ("Apple was worth $6.9 billion.", "$6.9 billion"),
    ("Apple was worth roughly $40B.", "$40B"),
    ("Apple shares rose 70%.", "70%"),
])
def test_a_number_stated_as_a_price_or_worth_claim_is_refused_whatever_the_anchors(text, detail):
    assert ("number_context", detail) in [(v.code, v.detail)
                                          for v in g.check_grounding("f", text, _SHEET)]


def test_the_draft_numbers_own_words_must_meet_the_source():
    """"cloud division" is in the draft sentence, but the 60% measures headcount there."""
    ctx = g.build_context(("About 60% of operating profit came from the cloud division.",),
                          frozenset("about of operating profit came from the cloud division"
                                    .split()))
    assert g.check_grounding("f", "Operating profit: 60% from the cloud.", ctx) == []
    got = [(v.code, v.detail) for v in g.check_grounding(
        "f", "The cloud division grew fast in those years, and later its total headcount rose "
        "60% after the launch.", ctx)]
    assert ("number_context", "60%") in got, got


def test_a_hedge_is_not_an_anchor():
    """Only "roughly" is shared with the source's "reported roughly forty billion dollars"."""
    assert ("number_context", "$40 billion") in [(v.code, v.detail) for v in g.check_grounding(
        "f", "Sales hit roughly $40 billion.", _SHEET)]


def test_the_subject_is_not_an_anchor():
    """Only "Apple" is shared: the Apple case study names Apple in every sentence."""
    ctx = g.build_context(("Apple opened 500 stores.", "Apple sold phones worldwide."),
                          frozenset("opened stores sold phones worldwide".split()))
    assert g.check_grounding("f", "Apple opened 500 new stores.", ctx) == []
    assert ("number_context", "500") in [(v.code, v.detail) for v in g.check_grounding(
        "f", "Apple hired 500 engineers.", ctx)]


def test_the_subject_is_not_an_anchor_on_the_draft_side_either():
    """A source row that is ONLY a subject and a number ("Tesla: 500.") falls back to its
    whole-sentence anchors, which are the subject; the draft's subject must still not meet it.
    (Round 2 changed this for a YEAR only — see the next test.)"""
    ctx = g.build_context(("Tesla: 500.", "Tesla sold cars."), frozenset({"sold", "cars"}))
    assert ("number_context", "500") in [(v.code, v.detail) for v in g.check_grounding(
        "f", "Tesla hired 500 staff.", ctx)]


def test_a_year_binds_on_its_subject_by_decision():
    """DECIDED in round 2 (W2-OB-1): a YEAR carries no price, value or return claim, and the
    subject is what identifies a dated event, so a year binds on any shared word of the two
    sentences, names included. The accepted cost is a same-company date misattribution ("Tesla
    hired staff in 2003" against "Tesla: 2003.") — a factual slip, never a price or value leak.
    What it buys: "LVMH bought Bulgari in 2011", "Amazon started as an online bookstore in 1994"
    and "CUDA, launched in 2006, …" — all real drafts — no longer fail on the verb."""
    ctx = g.build_context(("Tesla: 2003.", "Tesla sold cars."), frozenset({"sold", "cars"}))
    assert g.check_grounding("f", "Tesla hired staff in 2003.", ctx) == []
    # A year of ANOTHER subject still does not bind.
    assert ("number_context", "2003") in [(v.code, v.detail) for v in g.check_grounding(
        "f", "Ford hired staff in 2003.", ctx)]


def test_a_hyphen_joined_unit_word_is_still_the_unit():
    """"3-times" is not the unit-less count "3" (`_small_structural` reads past the hyphen)."""
    assert ("ungrounded_number", "3") in [(v.code, v.detail) for v in g.check_grounding(
        "f", "It grew 3-times over.", _SHEET)]


def test_worth_it_is_not_a_worth_claim():
    """Only "worth" + an amount is a value claim; "worth it" / "worth studying" are not."""
    ctx = g.build_context(("Costco charges members an annual fee of $65.",),
                          frozenset("costco charges members an annual fee of".split()))
    assert g.check_grounding("f", "Members say the $65 annual fee is worth it.", ctx) == []
    assert ("number_context", "$65") in [(v.code, v.detail) for v in g.check_grounding(
        "f", "The annual fee made each member worth $65.", ctx)]


def test_a_loss_cannot_come_back_as_a_profit():
    assert ("number_context", "$40B") in [(v.code, v.detail) for v in g.check_grounding(
        "f", "Reality Labs made a $40B profit.", _SHEET)]


@pytest.mark.parametrize("text", [
    "Services carried a gross margin near 70%.",
    "Renewal rates stayed above 90% in core markets.",
    "It paid $6.9B for Mellanox.",
    "The division lost about $40 billion from 2021 to 2023.",
    "Reality Labs losses reached $40B.",
])
def test_an_honest_paraphrase_still_grounds(text):
    assert g.check_grounding("f", text, _SHEET) == [], text


# ── idx 8: digit-free magnitudes ─────────────────────────────────────────────


@pytest.mark.parametrize("text, detail", [
    ("Apple became a trillion-dollar company.", "trillion"),
    ("Apple is a multi-trillion-dollar giant.", "multi-trillion"),
    ("Apple is worth billions.", "billions"),
    ("Apple is worth trillions.", "trillions"),
])
def test_a_magnitude_the_sheet_never_states_for_that_measure_is_ungrounded(text, detail):
    assert ("ungrounded_number", detail) in [(v.code, v.detail)
                                             for v in g.check_grounding("f", text, _SHEET)]


def test_a_magnitude_the_sheet_states_for_the_same_measure_grounds():
    assert g.check_grounding("f", "Apple sold billions of devices over the years.", _SHEET) == []


@pytest.mark.parametrize("text, relaxed", [
    ("NVIDIA became a trillion-dollar company.", True),     # names a company: every mode
    ("Apple joined the trillion-dollar club.", True),
    ("Apple became the world's largest company.", True),
    ("The stock hit an all-time high.", True),              # a price record: every mode
    ("It joined the trillion-dollar club.", False),         # no company: Money Moves only
    ("It became the world's largest company.", False),
])
def test_a_digit_free_market_cap_is_class_b(text, relaxed):
    assert "class_b_valuation" in [v.code for v in scan_text("f", clean(text))]
    got = [v.code for v in scan_text("f", clean(text), strict_instruments=False)]
    assert ("class_b_valuation" in got) is relaxed, (text, got)


@pytest.mark.parametrize("text", [
    "Inflation hit a record high in 1980.",
    "Rates fell to record lows.",
    "Sales hit an all-time high.",
    "A multi-billion-dollar factory was the entry ticket.",
])
def test_a_record_or_a_scale_that_is_not_a_market_cap_is_not_class_b(text):
    assert not [v for v in scan_text("f", clean(text)) if v.code.startswith("class_b")], text


# ── regression tests owed by earlier passes ──────────────────────────────────


def test_a_fraction_is_ordered():
    (a,) = extract_numbers("3/4")
    (b,) = extract_numbers("4/3")
    assert a.unit == b.unit == FRACTION and a.value == 0.75 and b.value == pytest.approx(4 / 3)
    assert not same_number(a, b)


def test_a_zero_denominator_is_a_number_nothing_equals():
    (m,) = extract_numbers("1/0")
    assert m.value == float("inf") and not same_number(m, m)


@pytest.mark.parametrize("text, expected", [
    ("a half million", "500000"),
    ("half a million", "500000"),
    ("two and a half", "2.5"),
    ("two and a half billion", "2500000000"),
])
def test_halves_convert_exactly(text, expected):
    assert words_to_digits(text) == expected


def test_form_4_is_a_name_not_a_quantity_and_must_be_grounded():
    assert extract_numbers("Form 4") == []
    ctx = g.build_context(("Insiders file reports.",), frozenset({"insiders", "file", "reports"}))
    assert ("ungrounded_name_number", "Form 4") in [
        (v.code, v.detail) for v in g.check_grounding("f", "Insiders file a Form 4.", ctx)]
    ctx4 = g.build_context(("Insiders file a Form 4 within two days.",),
                           frozenset({"insiders", "file", "a", "within", "two", "days"}))
    assert g.check_grounding("f", "Insiders file a Form 4.", ctx4) == []


def test_grounding_reports_too_long_and_scans_only_the_prefix():
    text = "It paid $6.9B for Mellanox. " + "a " * g.GROUNDING_CAP + "It fell 99%."
    got = [(v.code, v.detail) for v in g.check_grounding("f", text, _SHEET)]
    assert got[0][0] == "too_long"
    assert ("ungrounded_number", "99%") not in got     # past the cap: never scanned


def test_an_unexpected_error_fails_closed_as_grounding_error(monkeypatch, caplog):
    def boom(*_a, **_k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(g, "_check_companies", boom)
    with caplog.at_level(logging.ERROR, logger="app.services.marketing.grounding"):
        got = g.check_grounding("hook", "Apple is fine.", _SHEET)
    assert [(v.field, v.code, v.detail) for v in got][-1] == ("hook", "grounding_error",
                                                               "RuntimeError")
    assert any("check failed on field=hook" in r.getMessage() for r in caplog.records)
