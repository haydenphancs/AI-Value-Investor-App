"""Response models for `GET /api/v1/chat/starters`.

The daily-rotating suggestion chips shown above the "Ask Cay AI…" input on an empty
chat, plus the per-scope templates the five asset detail bars use.

Conventions follow the rest of `app/schemas/`: Pydantic v2, snake_case on the wire (the
Swift side decodes with explicit `CodingKeys`, so no aliases), and — the one that
matters here — **every field is defaulted**. This endpoint's entire contract is that it
degrades instead of failing: a build that loses every live market source still returns a
valid body carrying the evergreen questions, and an older iOS build that knows none of
these keys still decodes a newer response unchanged.
"""

from typing import List, Optional

from pydantic import BaseModel


class ChatStarterResponse(BaseModel):
    """One suggestion chip.

    `kind` exists so the client can style a live chip differently from an evergreen one
    without re-deriving that from the text. It is a plain `str` rather than an enum for
    the reason `schemas/chat.py` gives about `context_type`: an unknown value from a
    newer server must degrade on an older client, not 422 it.
    """

    text: str
    kind: str = "evergreen"          # hot_ticker | hot_sector | hot_topic | trending | evergreen | fixed
    # Present only on `hot_ticker`. Carried separately from `text` so the client never
    # has to parse a symbol back out of a sentence.
    symbol: Optional[str] = None


class DetailStarterSetResponse(BaseModel):
    """Per-surface question templates for the five asset detail AI bars.

    Every entry contains a literal ``{symbol}`` the client substitutes. iOS DROPS any
    template it cannot fill rather than rendering a raw brace, so adding a second
    placeholder here silently removes the question instead of breaking the screen.
    """

    ticker: List[str] = []
    etf: List[str] = []
    crypto: List[str] = []
    commodity: List[str] = []
    index: List[str] = []


class ChatStartersResponse(BaseModel):
    """The whole payload: one ET day's worth of starters for every surface.

    ⚠️ This body is **impersonal by contract**. One cache entry serves every caller, so
    nothing here may depend on who asked — no watchlist, no tier, no holdings. See the
    route docstring in `api/v1/endpoints/chat.py`; `test_chat_starters_endpoint.py`
    fails the build if the service starts reading the caller.
    """

    # ET calendar date the selection was computed for. iOS uses it as the refetch key,
    # so a device that stays open across midnight picks up the new set.
    trading_date: str = ""
    global_starters: List[ChatStarterResponse] = []
    detail_starters: DetailStarterSetResponse = DetailStarterSetResponse()
