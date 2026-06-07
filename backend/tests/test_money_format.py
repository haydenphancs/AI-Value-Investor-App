"""
Tests for _format_money_compact — the signed compact dollar formatter used in
the AI-facing evidence so the Bull/Bear thesis & Critical Factors quote
"-$394M" instead of "$-394,000,000". Large values abbreviate; sub-$1M values
are written out in full.
"""

from __future__ import annotations

import pytest

from app.services.agents.ticker_report_data_collector import _format_money_compact


@pytest.mark.parametrize("value,expected", [
    (-394_000_000, "-$394M"),       # the screenshot case (negative FCF)
    (394_000_000, "$394M"),
    (12_500_000, "$12.5M"),
    (1_000_000, "$1M"),
    (52_960_000_000, "$53B"),       # 1-decimal, trailing .0 stripped
    (1_234_000_000, "$1.2B"),
    (2_100_000_000_000, "$2.1T"),
    (-5_000_000_000, "-$5B"),
    (0, "$0"),
    # Under $1M — written out in full (with grouping commas).
    (250_000, "$250,000"),
    (999_999, "$999,999"),
    (-12_345, "-$12,345"),
])
def test_format_money_compact(value, expected):
    assert _format_money_compact(value) == expected


def test_format_money_compact_none_and_unparseable():
    assert _format_money_compact(None) == "N/A"
    assert _format_money_compact("not-a-number") == "N/A"
