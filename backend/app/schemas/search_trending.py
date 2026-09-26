"""
Search-screen chips: "Trending searches", "Most added" and the curated "Popular" fallback.

iOS decodes `SearchTrendingResponse` (`Models/SearchTrendingModels.swift`);
`tests/test_search_trending_schema_parity.py` pins the two sides together.

Every field has a default and `kind` is a plain string, not an enum: a newer server that
adds a section kind must not fail an older app's decode — the app drops kinds it does not
know. ⚠️ No COUNT field is ever sent. The lists rank tickers; they never say how many
people picked one (a small number would identify a tiny user base, and a count reads as
social proof).
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field

#: Section kinds, in the order the server may emit them. Equal to the iOS
#: `SearchTrendingKind` raw values (test-pinned).
SECTION_KINDS = ("trending_searches", "most_added", "popular")

#: What a pick may be. Equal to migration 179's CHECK on search_pick_daily.asset_type
#: (test-pinned). `index` and `commodity` are not searchable listings.
PICK_TYPES = ("stock", "etf", "fund", "crypto")

#: The symbol shape a pick or a chip may carry. Byte-equal to migration 179's CHECK on
#: search_pick_daily.ticker (test-pinned): the write path refuses anything the database
#: would, and the read path drops anything a client-writable watchlist row smuggled in.
SYMBOL_PATTERN = r"^[A-Z0-9][A-Z0-9.-]{0,9}$"


class SearchTrendingItemResponse(BaseModel):
    symbol: str
    name: str = ""
    type: str = "stock"


class SearchTrendingSectionResponse(BaseModel):
    kind: str
    items: List[SearchTrendingItemResponse] = Field(default_factory=list)


class SearchTrendingResponse(BaseModel):
    window_days: int = 7
    computed_at: str = ""
    # Every asset type — Home search, the ticker-search sheet, the Tracking add sheet.
    sections: List[SearchTrendingSectionResponse] = Field(default_factory=list)
    # Stocks only, thresholded AFTER filtering — the company picker and the Updates
    # "Add Ticker" sheet, which accept nothing else.
    stock_sections: List[SearchTrendingSectionResponse] = Field(default_factory=list)


class SearchPickRequest(BaseModel):
    """A tap on a search RESULT row. The caller's identity comes from the token only: a
    `user_id` in the body is ignored (`extra="ignore"`)."""

    model_config = ConfigDict(extra="ignore")

    symbol: str = Field(..., min_length=1, max_length=16)
    type: str = Field("stock", max_length=16)
