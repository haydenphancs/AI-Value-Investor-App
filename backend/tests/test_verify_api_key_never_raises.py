"""`verify_api_key` promises "EVERY key, and never a raise" — including non-ASCII input.

`secrets.compare_digest` raises `TypeError: comparing strings with non-ASCII characters is
not supported` on `str` operands, and Starlette decodes header values as latin-1, so any
byte >0x7F would have escaped as an unauthenticated 500 (the trap admin.py already works
around by comparing bytes).
"""
import pytest

from app.core.security import verify_api_key


@pytest.mark.parametrize("presented", ["ключ", "clé", "🔑", "abcÿ", "\x80"])
def test_non_ascii_input_is_simply_not_a_match(presented):
    assert verify_api_key(presented, {"abc", "def"}) is False


def test_non_ascii_candidates_do_not_raise_either():
    assert verify_api_key("abc", {"ключ", "abc"}) is True
    assert verify_api_key("ключ", {"ключ"}) is True


@pytest.mark.parametrize("presented, keys, expected", [
    ("", {"abc"}, False), (None, {"abc"}, False), ("abc", set(), False),
    ("b", {"a", "b"}, True), ("c", {"a", "b"}, False), (123, {"123"}, False),
])
def test_edge_inputs(presented, keys, expected):
    assert verify_api_key(presented, keys) is expected
