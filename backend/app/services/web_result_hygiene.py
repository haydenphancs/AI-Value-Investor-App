"""Pure hygiene for third-party web results — URL safety, the host policy, publisher names,
text cleaning and page dates.

Shared by Ask Cay AI's web search (`chat_web_search_service`, which re-exports these names so
`endpoints/chat.py` and the chat tests keep reading them there) and the Updates jump search
(`jump_web_search_service`, 2026-10-28). Moved out of the chat service so the jump search gets
the SAME deny-list and URL rules without importing the chat service, whose importers
`tests/test_brave_search_boundary.py` pins.

PURE: no network, no Supabase, no settings, no clock except where a function takes ``now``. Every
regex runs on a bounded string; `tests/test_chat_web_search_linearity.py` sweeps the
unanchored ones and pins the anchored-only ones to `.match` / `.fullmatch`.
"""

from __future__ import annotations

import html
import ipaddress
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

from app.services.chat_security import neutralize_fences

# ── Sizes ────────────────────────────────────────────────────────────────────
_PUBLISHER_CAP = 60
_URL_MAX = 2048
#: A page date longer than this is not one (bounds every parse below).
_PAGE_AGE_MAX_CHARS = 40

# ── Host policy ──────────────────────────────────────────────────────────────
_DENIED_HOSTS = frozenset({
    "reddit.com", "redd.it", "x.com", "twitter.com", "t.co", "stocktwits.com",
    "youtube.com", "youtu.be", "tiktok.com", "facebook.com", "fb.com", "fb.watch",
    "instagram.com", "threads.net", "quora.com", "medium.com", "substack.com", "linkedin.com",
    "pinterest.com", "discord.com", "discord.gg", "t.me", "telegram.org", "tumblr.com",
    "4chan.org", "investorshub.advfn.com", "hotcopper.com.au",
    # search engines
    "brave.com", "google.com", "bing.com", "duckduckgo.com", "search.yahoo.com",
    # quote / price pages as a whole site (brokers, price trackers)
    "robinhood.com", "webull.com", "companiesmarketcap.com", "coinmarketcap.com",
    "coingecko.com", "markets.ft.com",
})
# Quote / price pages on otherwise legitimate publishers — the code backstop for "never take a
# price from the web" (host suffix, path prefix).
_QUOTE_PATHS: Tuple[Tuple[str, str], ...] = (
    ("finance.yahoo.com", "/quote/"),
    ("marketwatch.com", "/investing/"),
    ("nasdaq.com", "/market-activity/"),
    ("cnbc.com", "/quotes/"),
    ("investing.com", "/equities/"), ("investing.com", "/indices/"),
    ("investing.com", "/commodities/"), ("investing.com", "/currencies/"),
    ("investing.com", "/crypto/"), ("investing.com", "/etfs/"),
    ("bloomberg.com", "/quote/"),
    ("finviz.com", "/quote"),
    ("stockanalysis.com", "/stocks/"), ("stockanalysis.com", "/etf/"),
    ("tradingview.com", "/symbols/"),
    ("marketbeat.com", "/stocks/"),
    ("zacks.com", "/stock/quote/"),
    ("morningstar.com", "/stocks/"), ("morningstar.com", "/etfs/"), ("morningstar.com", "/funds/"),
    ("seekingalpha.com", "/symbol/"),
    ("fool.com", "/quote/"),
    ("wsj.com", "/market-data/"),
    ("barrons.com", "/market-data/"), ("barrons.com", "/quote/"),
    ("macrotrends.net", "/stocks/"),
    ("reuters.com", "/markets/companies/"),
)
# Display names for well-known outlets, matched on the URL's own host (exact or a subdomain).
# Anything else shows its bare host — never the page's self-description.
_PUBLISHER_BY_HOST: Dict[str, str] = {
    "reuters.com": "Reuters", "apnews.com": "AP News", "bloomberg.com": "Bloomberg",
    "wsj.com": "The Wall Street Journal", "ft.com": "Financial Times", "cnbc.com": "CNBC",
    "nytimes.com": "The New York Times", "washingtonpost.com": "The Washington Post",
    "bbc.com": "BBC", "bbc.co.uk": "BBC", "theguardian.com": "The Guardian",
    "economist.com": "The Economist", "forbes.com": "Forbes", "fortune.com": "Fortune",
    "businessinsider.com": "Business Insider", "marketwatch.com": "MarketWatch",
    "barrons.com": "Barron's", "finance.yahoo.com": "Yahoo Finance", "yahoo.com": "Yahoo",
    "investopedia.com": "Investopedia", "sec.gov": "SEC", "techcrunch.com": "TechCrunch",
    "theverge.com": "The Verge", "axios.com": "Axios", "politico.com": "Politico",
    "fool.com": "The Motley Fool", "morningstar.com": "Morningstar",
    "seekingalpha.com": "Seeking Alpha", "investors.com": "Investor's Business Daily",
    "zacks.com": "Zacks", "kiplinger.com": "Kiplinger", "npr.org": "NPR", "cnn.com": "CNN",
    "foxbusiness.com": "Fox Business", "nasdaq.com": "Nasdaq", "prnewswire.com": "PR Newswire",
    "businesswire.com": "Business Wire", "globenewswire.com": "GlobeNewswire",
    "ftc.gov": "FTC", "justice.gov": "U.S. Department of Justice",
    "federalreserve.gov": "Federal Reserve", "wired.com": "Wired",
    "arstechnica.com": "Ars Technica", "latimes.com": "Los Angeles Times",
    "nikkei.com": "Nikkei", "scmp.com": "South China Morning Post",
    "aljazeera.com": "Al Jazeera", "theinformation.com": "The Information",
    "semafor.com": "Semafor", "investing.com": "Investing.com",
}
_PUBLISHER_KEYS_LONGEST_FIRST = sorted(_PUBLISHER_BY_HOST, key=len, reverse=True)

# ── Text hygiene ─────────────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^<>]{0,500}>")
_URL_TEXT_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
# A snippet that tries to talk to the model is dropped with its whole result.
_INJECTION_RE = re.compile(
    r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+"
    r"(?:instructions?|prompts?|rules?|messages?)\b|\bsystem\s+prompt\b|\byou\s+are\s+now\b|"
    r"\bnew\s+instructions?\s*:",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")
# Brave's `age` text: "35 minutes ago", "2 hours ago", "an hour ago" — and "1 day ago", which is
# deliberately NOT read: Brave counts whole days (24-47 h is "1 day ago"), so as an instant it
# would be a guess that passes a 24 h window (review 2026-10-29).
_RELATIVE_AGE_RE = re.compile(
    r"^\s*(\d{1,3}|an?|one)\s+(minute|min|hour|hr)s?\s+ago\s*$", re.IGNORECASE,
)
_RELATIVE_UNIT_SECONDS = {"minute": 60, "min": 60, "hour": 3600, "hr": 3600}

_HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST_SHAPE_RE = re.compile(rf"(?:{_HOST_LABEL}\.)+(?:[a-z]{{2,63}}|xn--[a-z0-9-]{{1,59}})")


def _cap(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: max(cap - 1, 1)].rstrip() + "…"


def _clean_text(value: Any, cap: int) -> Optional[str]:
    """Third-party text made safe for the model: no tags (even double-encoded ones), no URL, no
    fence delimiter, one line, capped. None when nothing is left."""
    if not isinstance(value, str):
        return None
    t = _TAG_RE.sub(" ", value)
    t = html.unescape(t)
    t = _TAG_RE.sub(" ", t)          # `&lt;script&gt;` decodes into a tag
    t = _URL_TEXT_RE.sub(" ", t)
    t = neutralize_fences(t)
    t = _WS_RE.sub(" ", t).strip()
    if not t:
        return None
    return _cap(t, cap)


def _safe_https_url(url: Any) -> Optional[Tuple[str, str, str]]:
    """(url, host, path) for an https URL a pill may open, else None. The host comes from the URL
    itself — lowercased, trailing dot stripped, IDNA-encoded — never from Brave's `meta_url`."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u or len(u) > _URL_MAX or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in u):
        return None
    try:
        parts = urlsplit(u)
        if parts.scheme.lower() != "https":
            return None
        if "@" in parts.netloc or parts.username is not None or parts.password is not None:
            return None
        if parts.port is not None:
            return None
        host = (parts.hostname or "").strip().rstrip(".").lower()
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return None
    try:
        ipaddress.ip_address(host.strip("[]"))
        return None              # an IP literal is never a publisher
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None
    if len(host) > 253 or not _HOST_SHAPE_RE.fullmatch(host):
        return None
    return u, host, (parts.path or "/").lower()


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def _host_denied(host: str, path: str) -> bool:
    if any(_host_matches(host, d) for d in _DENIED_HOSTS):
        return True
    return any(_host_matches(host, h) and path.startswith(p) for h, p in _QUOTE_PATHS)


def _publisher_for_host(host: str) -> str:
    for key in _PUBLISHER_KEYS_LONGEST_FIRST:
        if _host_matches(host, key):
            return _PUBLISHER_BY_HOST[key]
    bare = host[4:] if host.startswith("www.") else host
    return _cap(bare, _PUBLISHER_CAP)


def _known_publisher(host: str) -> bool:
    """Whether ``host`` belongs to a publisher we name (`_PUBLISHER_BY_HOST`)."""
    return any(_host_matches(host, key) for key in _PUBLISHER_KEYS_LONGEST_FIRST)


def _iso_date(value: Any) -> Optional[str]:
    """`YYYY-MM-DD` from an ISO timestamp, only when it is a real date between 1990 and next
    year; else None (a relative "2 days ago" is not a date)."""
    if not isinstance(value, str):
        return None
    m = _ISO_DATE_RE.match(value)
    if not m:
        return None
    try:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if not 1990 <= d.year <= datetime.now(timezone.utc).year + 1:
        return None
    return d.strftime("%Y-%m-%d")


def parse_page_age(value: Any) -> Optional[datetime]:
    """An aware UTC instant from a page's ISO `page_age` ("2026-10-28T18:21:00"; a naive value is
    read as UTC, an offset is honoured), else None.

    None — never a guess — for a date with no time ("2026-10-28") and for an exact naive midnight
    ("2026-10-28T00:00:00"): both are how a page states only its DAY, and read as an instant they
    would stamp a story published at noon as 8 p.m. ET the evening before. Also None for a
    relative text, an unparseable value, or a year outside 1990..next year.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) <= 10 or len(text) > _PAGE_AGE_MAX_CHARS:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        if (dt.hour, dt.minute, dt.second, dt.microsecond) == (0, 0, 0, 0):
            return None
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        # An offset at the edge of the calendar ("0001-01-01T00:30:00+01:00") overflows here.
        dt = dt.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None
    if not 1990 <= dt.year <= datetime.now(timezone.utc).year + 1:
        return None
    return dt


def parse_relative_age(value: Any, now: datetime) -> Optional[datetime]:
    """The instant a relative age text names ("35 minutes ago", "an hour ago", "3 hrs ago"),
    measured back from ``now``; None for anything else — a day count included, since "1 day
    ago" means anywhere from 24 to 47 hours. ``now`` must be aware."""
    if not isinstance(value, str) or len(value) > _PAGE_AGE_MAX_CHARS:
        return None
    m = _RELATIVE_AGE_RE.match(value)
    if not m:
        return None
    raw = m.group(1).lower()
    count = 1 if raw in ("a", "an", "one") else int(raw)
    return now - timedelta(seconds=count * _RELATIVE_UNIT_SECONDS[m.group(2).lower()])
