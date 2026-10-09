"""Plain-English Form 4 trade wording for Ask Cay AI (`_insider_common.plain_transaction_phrase`).

Why (2026-10-08): the ownership tool printed the raw code ("F-InKind: disposed of 12,000
shares"), and a model reads "disposed of" beside a dollar figure as a SALE — or calls an
open-market sale's dollars "the tax". One code table now drives both the Holders tab's
classifier and these phrases, so chat can never word a row the tab counts differently.

Pure: no network, no Supabase.
"""

from __future__ import annotations

import math

import pytest

from app.services._insider_common import (
    classify_insider_transaction,
    plain_transaction_phrase,
    transaction_family,
)


def _old_classifier(tx_type):
    """The classifier exactly as it stood before the shared table (the oracle)."""
    tx = (tx_type or "").strip().upper()
    if tx.startswith("P"):
        return "Informative Buy"
    if tx.startswith("S"):
        if "+OE" in tx or "+DIS" in tx or "EXEMPT" in tx:
            return "Uninformative Sell"
        return "Informative Sell"
    if tx.startswith(("A", "M", "G")):
        return "Uninformative Buy"
    if tx.startswith(("F", "D")):
        return "Uninformative Sell"
    return "Uninformative Sell"


_CODES = [
    "P-Purchase", "S-Sale", "S-Sale+OE", "S-Sale+DIS", "S-Exempt", "s-sale", " S-Sale ",
    "A-Award", "M-Exempt", "M-OptEx", "G-Gift", "F-InKind", "F-TaxWithholding", "D-Return",
    "C-Conversion", "J-Other", "X-InTheMoney", "W-Will", "I-Discretionary", "K-Swap", "",
    None, "P", "S", "F", "Z", "  ", "SALE", "Purchase",
]


@pytest.mark.parametrize("code", _CODES)
def test_the_classifier_is_unchanged_by_the_shared_table(code):
    assert classify_insider_transaction(code) == _old_classifier(code)


def test_a_non_string_code_never_raises():
    for code in (5, 3.2, ["S"], {"S": 1}, True):
        assert classify_insider_transaction(code) == "Uninformative Sell"
        assert transaction_family(code) == "other"
        assert plain_transaction_phrase(code, 10, 1.0).startswith("reported a transaction")


# ── the wording that must not go wrong ──────────────────────────────────────────────

def test_an_open_market_sale_is_worded_as_proceeds_never_tax():
    text = plain_transaction_phrase("S-Sale", 65616, 87.69)
    assert text == ("sold 65,616 shares in the open market at an average $87.69 "
                    "(about $5.75 million in sale proceeds)")
    assert "tax" not in text.lower()


@pytest.mark.parametrize("code", ["F-InKind", "F-TaxWithholding", "f-inkind"])
@pytest.mark.parametrize("price", [87.69, None, 0, float("nan")])
def test_tax_withholding_is_never_a_sale_and_never_carries_proceeds(code, price):
    text = plain_transaction_phrase(code, 12000, price, acquired=False)
    low = text.lower()
    assert "withheld" in low and "taxes" in low and "not a sale" in low
    assert "sold" not in low and "proceeds" not in low
    assert "no tax amount" in low
    # A per-share value at most — never a dollar total that reads like "the tax".
    assert "million" not in low and "$1,052" not in text
    if price == 87.69:
        assert "valued at $87.69 a share" in text
    else:
        assert "$" not in text


def test_a_purchase_states_what_was_paid():
    assert plain_transaction_phrase("P-Purchase", 1000, 10.0) == (
        "bought 1,000 shares in the open market at an average $10.00 (about $10,000 paid)")


@pytest.mark.parametrize("code, expected", [
    ("S-Sale+OE", "sold 500 shares at an average $20.00 as part of an option exercise"),
    ("S-Sale+DIS", "sold 500 shares at an average $20.00 as part of a stock-award settlement"),
    ("S-Exempt", "sold 500 shares at an average $20.00 in an exempt transaction"),
])
def test_composite_sales_say_what_they_are(code, expected):
    assert plain_transaction_phrase(code, 500, 20.0) == expected


def test_the_other_codes():
    assert plain_transaction_phrase("A-Award", 7608, None) == "received 7,608 shares as a grant or award"
    assert plain_transaction_phrase("M-Exempt", 17391, 0.0, acquired=True) == (
        "acquired 17,391 shares through an option or restricted-stock-unit exercise or conversion")
    assert plain_transaction_phrase("M-Exempt", 5, None, acquired=False).startswith("disposed of 5 shares")
    assert plain_transaction_phrase("G-Gift", 62500, None, acquired=False) == "gave 62,500 shares as a gift"
    assert plain_transaction_phrase("G-Gift", 62500, None, acquired=True) == "received 62,500 shares as a gift"
    assert plain_transaction_phrase("G-Gift", 62500, None) == "reported a gift of 62,500 shares"
    assert plain_transaction_phrase("D-Return", 100, None) == "returned 100 shares to the company"
    assert plain_transaction_phrase("C-Conversion", 62500, None, acquired=True).startswith(
        "received 62,500 shares in a conversion")
    assert plain_transaction_phrase("C-Conversion", 62500, None, acquired=False).startswith(
        "converted 62,500 shares into another")
    assert plain_transaction_phrase("J-Other", 3, None) == "reported a transaction of 3 shares (code J-Other)"
    assert plain_transaction_phrase("", 3, None) == "reported a transaction of 3 shares"
    assert plain_transaction_phrase(None, None, None) == (
        "reported a transaction of an unreported number of shares")


# ── outliers: never a fabricated 0, never a crash ──────────────────────────────────────

@pytest.mark.parametrize("shares", [None, float("nan"), float("inf"), -5, "12", True, [], {}])
def test_an_unusable_share_count_is_unreported_never_zero(shares):
    text = plain_transaction_phrase("S-Sale", shares, 10.0)
    assert "an unreported number of shares" in text
    assert "proceeds" not in text   # no total without a count


@pytest.mark.parametrize("price", [None, 0, -3.0, float("nan"), float("inf"), True, "x"])
def test_an_unusable_price_is_left_out(price):
    text = plain_transaction_phrase("S-Sale", 100, price)
    assert text == "sold 100 shares in the open market"


def test_singular_fractional_and_huge_counts():
    assert plain_transaction_phrase("P-Purchase", 1, None) == "bought 1 share in the open market"
    assert plain_transaction_phrase("A-Award", 12.5, None) == "received 12.5 shares as a grant or award"
    text = plain_transaction_phrase("S-Sale", 1e12, 1e3)
    assert "1,000,000,000,000 shares" in text and "billion in sale proceeds" in text
    assert plain_transaction_phrase("S-Sale", 0, 10.0) == "sold 0 shares in the open market at an average $10.00"


def test_a_long_unknown_code_is_capped():
    text = plain_transaction_phrase("Q" * 500, 1, None)
    assert len(text) < 80 and math.isfinite(len(text))
