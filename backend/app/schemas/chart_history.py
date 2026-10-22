"""
Chart bars on the wire: the paged history (`GET /{asset}/{symbol}/chart/history`) and the
plain range responses (`GET /stocks/{ticker}/chart`, `GET /crypto/{symbol}/chart`).

One bar shape for all three, the shape iOS `StockPricePoint` already decodes:
``{date, open?, high?, low?, close, volume?}``. ``close`` is required and always finite
and > 0 (every producer drops a bar without one); open/high/low/volume are None when the
source did not report them — never 0 and never the close.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class ChartBar(BaseModel):
    """One bar. ``date`` is ``YYYY-MM-DD`` for daily and coarser (a weekly/monthly bucket
    is dated by its LAST trading day) and ``YYYY-MM-DD HH:MM:SS`` (ET wall clock) intraday."""

    date: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: float
    volume: Optional[float] = None


class ChartHistoryResponse(BaseModel):
    """One page of bars strictly OLDER than the request's ``before`` cursor.

    * ``bars`` — oldest first; the next cursor is ``bars[0].date``.
    * ``interval`` — the interval actually served (a crypto pair is always hourly
      intraday, whatever finer size was asked).
    * ``has_more`` — false only when the series is non-empty and this page reached its
      first bar (or, intraday, its provider depth). A failed or empty source is an error
      response, never ``has_more: false``.
    * ``settled_through`` — the last settled session behind a daily-or-coarser page (the
      close cycle); a client compares it across pages to catch a split re-adjusting the
      series. None for crypto.
    * ``reached_start`` — true only when this page holds the FIRST bar the source has for
      the symbol (its listing, as far as the provider knows; intraday, only when the stock's
      daily history begins that same day). ``has_more: false`` alone also covers paging that
      stopped short of it: the intraday look-back bound, empty intraday windows over a span
      the daily history covers, CoinGecko's plan cap, the 90-day hourly crypto series. The client
      lets its indicator floor go to the leftmost bar only on this flag — at any other
      stop, the first ~210 bars are warm-up whose MA/RSI/MACD would differ from the
      full-history values.
    """

    symbol: str
    interval: str
    bars: List[ChartBar] = Field(default_factory=list)
    has_more: bool
    settled_through: Optional[str] = None
    reached_start: bool = False


class ChartPricesResponse(BaseModel):
    """One range's bars, oldest first (warm-up bars before the visible window included)."""

    symbol: str
    prices: List[ChartBar] = Field(default_factory=list)
