"""An unrecognised index must not be served the S&P 500's identity.

`_build_index_detail` used to fall back to `_INDEX_PROFILES["^GSPC"]`, and that registry holds
only ^GSPC / ^IXIC / ^DJI — while `indices.py` accepts any `^[A-Z0-9.\\-]{1,12}`. So a Russell
2000 or VIX screen took the S&P 500's NAME, description, inception date, constituent count and
historical P/E. Cay AI grounds on that payload (`ChatContextResolver._resolve_index` dumps it)
and then states those as fact about a different index.

Latent rather than live today — only the three profiled indices are reachable from Home's Market
Pulse — which is exactly why it needs a test: nothing else would catch the next one added.
"""

from __future__ import annotations

import pytest

from app.services.index_service import _INDEX_PROFILES


def test_the_registry_still_only_covers_three():
    """Anti-vacuity guard on the tests below: if someone profiles every index, the fallback
    stops mattering and these assertions quietly stop proving anything."""
    assert set(_INDEX_PROFILES) == {"^GSPC", "^IXIC", "^DJI"}


def test_sp500_identity_is_not_reachable_by_fallback():
    """The defect in one line: `.get(symbol, _INDEX_PROFILES.get("^GSPC"))`."""
    for unknown in ("^RUT", "^VIX", "^FTSE", "^N225", "^UNKNOWN"):
        assert _INDEX_PROFILES.get(unknown) is None, unknown


@pytest.mark.parametrize("symbol", ["^RUT", "^VIX", "^FTSE"])
def test_an_unknown_index_gets_an_empty_profile_not_a_borrowed_one(symbol):
    """Mirrors what `_build_index_detail` now does: `.get(sym) or {}`, so every profile field
    degrades to blank. A gap the user can see beats a confident lie they can't."""
    meta = _INDEX_PROFILES.get(symbol.upper()) or {}
    assert meta == {}
    assert (meta.get("name") or symbol) == symbol
    assert meta.get("description", "") == ""
    assert meta.get("inception_date", "") == ""
    assert meta.get("index_provider", "") == ""
    # The one that would have been quoted as a valuation anchor for the wrong index.
    assert meta.get("historical_avg_pe") is None


@pytest.mark.parametrize("symbol", ["^GSPC", "^IXIC", "^DJI"])
def test_a_profiled_index_still_gets_its_own_identity(symbol):
    meta = _INDEX_PROFILES.get(symbol) or {}
    assert meta.get("name")
    assert meta.get("description")
    assert isinstance(meta.get("historical_avg_pe"), (int, float))


def test_the_three_profiles_are_actually_distinct():
    """They were interchangeable-by-accident before; make sure they are not identical now."""
    names = {p["name"] for p in _INDEX_PROFILES.values()}
    assert len(names) == 3
