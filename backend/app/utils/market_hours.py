"""
US Market Hours Utility
Determines whether US equity markets are in an active trading session.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# US market holidays for 2025-2026 (NYSE/NASDAQ closed)
US_MARKET_HOLIDAYS = {
    # 2025
    (2025, 1, 1),   # New Year's Day
    (2025, 1, 20),  # MLK Day
    (2025, 2, 17),  # Presidents' Day
    (2025, 4, 18),  # Good Friday
    (2025, 5, 26),  # Memorial Day
    (2025, 6, 19),  # Juneteenth
    (2025, 7, 4),   # Independence Day
    (2025, 9, 1),   # Labor Day
    (2025, 11, 27), # Thanksgiving
    (2025, 12, 25), # Christmas
    # 2026
    (2026, 1, 1),   # New Year's Day
    (2026, 1, 19),  # MLK Day
    (2026, 2, 16),  # Presidents' Day
    (2026, 4, 3),   # Good Friday
    (2026, 5, 25),  # Memorial Day
    (2026, 6, 19),  # Juneteenth
    (2026, 7, 3),   # Independence Day (observed)
    (2026, 9, 7),   # Labor Day
    (2026, 11, 26), # Thanksgiving
    (2026, 12, 25), # Christmas
}


def is_market_active() -> bool:
    """
    Check if US equity markets are in an active trading session.

    Returns True during:
    - Pre-market:  4:00 AM – 9:30 AM ET
    - Regular:     9:30 AM – 4:00 PM ET
    - After-hours: 4:00 PM – 8:00 PM ET

    Returns False during overnight (8 PM – 4 AM ET), weekends, and holidays.
    """
    now = datetime.now(ET)

    # Weekend check (Saturday=5, Sunday=6)
    if now.weekday() >= 5:
        return False

    # Holiday check
    if (now.year, now.month, now.day) in US_MARKET_HOLIDAYS:
        return False

    # Active window: 4:00 AM (240 min) to 8:00 PM (1200 min) ET
    minute_of_day = now.hour * 60 + now.minute
    return 240 <= minute_of_day < 1200
