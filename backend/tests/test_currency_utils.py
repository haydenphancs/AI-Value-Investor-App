"""`app.utils.currency` — ONE currency-code rule for every reader of an FMP currency field.

Three private copies used to disagree (2026-10-08 R1 review): the report's collector refused
"usd" that the Overview read as "USD"; the Overview upper-cased BEFORE its ASCII check, so
"ßU" became the valid-looking code "SSU"; report chat accepted only an already-canonical value.
The wrappers stay (tests and callers import their names) but must all BE the shared rule.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from app.utils.currency import currency_code, money_prefix

_CASES = [
    ("USD", "USD"), ("TWD", "TWD"), ("usd", "USD"), ("Usd", "USD"), (" twd\t", "TWD"),
    ("\nEUR\n", "EUR"), ("jPy", "JPY"),
    ("US$", None), ("N/A", None), ("", None), ("   ", None), ("USDT", None), ("US", None),
    ("U S", None), ("U1D", None), ("12A", None), ("$$$", None), ("US-", None),
    ("ÜSD", None), ("ÉUR", None), ("ＵＳＤ", None),            # full-width letters
    (None, None), (True, None), (False, None), (840, None), (8.4, None), (["USD"], None),
    ({"code": "USD"}, None), (b"USD", None),
    (" " * 100_000, None), ("A" * 100_000, None), ("U" * 3 + " " * 50_000, "UUU"),
]


@pytest.mark.parametrize("raw,expected", _CASES)
def test_currency_code(raw, expected):
    assert currency_code(raw) == expected


@pytest.mark.parametrize("raw", ["ßU", "ﬁX", "ﬀA", "ﬆD", "ßS", "ﬃ"])
def test_a_letter_that_upper_cases_into_ascii_is_never_a_code(raw):
    """"ß".upper() == "SS", "ﬁ".upper() == "FI", "ﬃ".upper() == "FFI": a non-ASCII value whose
    upper-case IS three ASCII letters must stay unknown (the old Overview rule read "SSU")."""
    assert raw.upper().isascii() and len(raw.upper()) == 3, "the case would not be a trap"
    assert currency_code(raw) is None


@pytest.mark.parametrize("raw", ["ǆA", "ŉU", "İSD", "ıSD"])
def test_other_non_ascii_letters_are_never_a_code(raw):
    assert currency_code(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("USD", "$"), ("usd", "$"), (None, "$"), ("", "$"), ("garbage", "$"), ("ßU", "$"),
    (42, "$"), ("TWD", "TWD "), (" eur ", "EUR "), ("JPY", "JPY "),
])
def test_money_prefix(raw, expected):
    """USD and an UNKNOWN currency keep "$" (what every surface printed before); a known
    non-USD code replaces it with the code and a space."""
    assert money_prefix(raw) == expected


def _wrappers():
    from app.services import chat_context_resolver as R
    from app.services import company_facts_service as F
    from app.services import stock_overview_service as O
    from app.services.agents import ticker_report_data_collector as C

    return {
        "collector": C._currency_code, "overview": O._currency_code,
        "resolver": R._currency_code, "company_facts": F._clean_currency,
    }


@pytest.mark.parametrize("raw,expected", _CASES + [("ßU", None), ("ﬁX", None)])
def test_every_surface_reads_one_feed_value_alike(raw, expected):
    for name, fn in _wrappers().items():
        assert fn(raw) == expected, (name, raw)


def _body_without_docstring(fn) -> str:
    tree = ast.parse(inspect.getsource(fn).lstrip())
    node = tree.body[0]
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None),
                                                              ast.Constant):
        body = body[1:]
    return "\n".join(ast.unparse(b) for b in body)


@pytest.mark.parametrize("name", ["collector", "overview", "resolver", "company_facts"])
def test_each_wrapper_delegates_to_the_shared_rule(name):
    """Source-bound (docstring stripped by AST, so prose cannot satisfy it): the wrapper's body
    is the one call — a private rule re-grown inside it fails here even if today's inputs agree."""
    body = _body_without_docstring(_wrappers()[name])
    assert body == "return currency_code(raw)" or body == "return currency_code(value)", body
