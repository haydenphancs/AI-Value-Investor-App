"""
The public smart link `/go/{campaign}`, the landing page at `/`, and the root-route allowlist
(SYSTEM_DESIGN_GUIDELINES §12.6, rules marketing.md §1).

Why each group exists:

* **302, exact Location.** 307 (RedirectResponse's default) would make a crawler replay a HEAD
  or a POST; the brand's link must be a plain, uncached, cookie-free 302. Pre-launch it lands on
  `/`; post-launch on the App Store URL, with App Analytics campaign params only when a token is
  configured and only for a KNOWN campaign.
* **Hostile campaigns.** The path segment is attacker-typed. Nothing of it may reach `Location`
  (open redirect / header injection) — `ct` is always a constant.
* **Counting.** Every platform's link-preview crawler fetches a post's link, so an uncounted
  crawler is the difference between a real number and noise. They still get the 302.
* **Flush.** The counter is in memory; a failed RPC must keep its counts, and a database without
  migration 173 must say so ONCE instead of every minute.
* **Landing page.** Public, unauthenticated, on the passkey domain: no script, no external
  resource, and copy that passes the same compliance lists as every public post (no FMP data,
  no named people, no return figures).
* **Root-route allowlist.** `tests/test_account_only_licence_gate.py` only scans `/api/v1`. A new
  unauthenticated route at the root would serve anonymous callers with nobody noticing, so every
  root route must be listed here with a reason.

Hermetic: `TestClient(app)` WITHOUT the context manager (no lifespan), a fake Supabase client
patched at the binding `smart_link` uses, and the process-wide rate limiter cleared per test.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.config import settings
from app.core.security import rate_limiter
from app.schemas.marketing import POST_PLATFORMS
from app.services.marketing import compliance, smart_link
from app.services.notification_kinds import NOTIFICATION_KINDS
from app.utils.market_hours import ET

_BACKEND = Path(__file__).resolve().parents[1]
_TEMPLATE = _BACKEND / "app" / "templates" / "site" / "index.html"
_LOGGER = "app.services.marketing.smart_link"

_STORE = "https://apps.apple.com/us/app/caydex/id6759525689"
_TOKEN = "118282305"
_DAY = "2026-09-23"
_BROWSER = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
_DISCLAIMER = (
    "Caydex · Educational information only — not investment advice. Investing involves risk."
)


# ── fixtures / helpers ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Every piece of module state this file touches starts clean and is restored."""
    monkeypatch.setattr(smart_link, "_pending", {})
    monkeypatch.setattr(smart_link, "_retry", {})
    monkeypatch.setattr(smart_link, "_windows", {})
    monkeypatch.setattr(smart_link, "_cap_warned", False)
    monkeypatch.setattr(smart_link, "_missing_logged", False)
    monkeypatch.setattr(smart_link, "_misconfig_logged", False)
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", "")
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_PROVIDER_TOKEN", "")
    rate_limiter.clear()
    smart_link._link_limiter.clear()
    yield
    rate_limiter.clear()
    smart_link._link_limiter.clear()


@pytest.fixture
def client():
    # Resolved at test time: tests/test_cors_and_security_headers.py reloads app.main.
    import app.main as main_mod

    return TestClient(main_mod.app)


@pytest.fixture
def launched(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", _STORE)


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_PROVIDER_TOKEN", _TOKEN)


@pytest.fixture
def fixed_day(monkeypatch):
    monkeypatch.setattr(smart_link, "_today_et", lambda: _DAY)


def _go(client, path, *, method="GET", ua=_BROWSER, ip="203.0.113.7", headers=None):
    h = {"User-Agent": ua, "X-Forwarded-For": ip}
    h.update(headers or {})
    return client.request(method, path, headers=h, follow_redirects=False)


def _assert_plain_302(r):
    assert r.status_code == 302, (r.status_code, r.text[:200])
    assert r.headers.get("cache-control") == "no-store"
    assert "set-cookie" not in {k.lower() for k in r.headers}
    assert r.content == b""
    # Baseline security headers still apply (the middleware covers every response).
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("x-content-type-options") == "nosniff"


# ── 1. the redirect itself ────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("path", ["/go/tiktok", "/go"])
def test_go_is_a_plain_302_never_307(client, method, path):
    r = _go(client, path, method=method)
    _assert_plain_302(r)
    assert r.headers["location"] == "/"


@pytest.mark.parametrize("campaign", sorted(POST_PLATFORMS))
def test_prelaunch_every_campaign_lands_on_the_landing_page(client, campaign):
    r = _go(client, f"/go/{campaign}")
    _assert_plain_302(r)
    assert r.headers["location"] == "/"


@pytest.mark.parametrize("path", ["/go/tiktok", "/go", "/go/newsletter"])
def test_post_launch_without_a_token_is_the_bare_store_url(client, launched, path):
    r = _go(client, path)
    _assert_plain_302(r)
    assert r.headers["location"] == _STORE


@pytest.mark.parametrize("campaign", sorted(POST_PLATFORMS))
def test_post_launch_with_a_token_appends_pt_ct_mt_for_known_campaigns(
        client, launched, token, campaign):
    r = _go(client, f"/go/{campaign}")
    _assert_plain_302(r)
    assert r.headers["location"] == f"{_STORE}?pt={_TOKEN}&ct={campaign}&mt=8"


@pytest.mark.parametrize("path", ["/go", "/go/newsletter", "/go/other"])
def test_campaign_params_are_never_added_for_an_unknown_campaign(client, launched, token, path):
    r = _go(client, path)
    _assert_plain_302(r)
    assert r.headers["location"] == _STORE


def test_an_existing_query_is_kept_and_ours_override_its_campaign_keys(monkeypatch, token):
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", f"{_STORE}?l=en&ct=evil&mt=1")
    loc = smart_link.destination("youtube")
    parts = urlsplit(loc)
    assert (parts.scheme, parts.hostname, parts.path) == ("https", "apps.apple.com",
                                                          "/us/app/caydex/id6759525689")
    assert parse_qsl(parts.query) == [("l", "en"), ("pt", _TOKEN), ("ct", "youtube"), ("mt", "8")]


def test_destination_renormalises_so_ct_can_only_be_a_constant(launched, token):
    # A future caller that forgets normalize_campaign still cannot put request text in `ct`.
    assert smart_link.destination("../evil") == _STORE
    assert smart_link.destination("TikTok") == _STORE
    assert smart_link.destination("tiktok") == f"{_STORE}?pt={_TOKEN}&ct=tiktok&mt=8"


def test_a_token_with_reserved_characters_is_encoded_not_spliced(monkeypatch, launched):
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_PROVIDER_TOKEN", "12&ct=evil#x")
    loc = smart_link.destination("x")
    assert parse_qsl(urlsplit(loc).query) == [("pt", "12&ct=evil#x"), ("ct", "x"), ("mt", "8")]
    assert urlsplit(loc).fragment == ""


# ── 2. hostile campaigns ──────────────────────────────────────────────────────

# (raw path segment as sent, the campaign it must normalise to — None means "other")
_HOSTILE = [
    ("TikTok", "tiktok"),
    ("%20tiktok", "tiktok"),
    ("x%0A", "x"),
    ("%2Fevil.com", None),
    ("%2F%2Fevil.com", None),
    ("..%2F..", None),
    ("%2E%2E", None),                             # ".." kept encoded past the client
    ("evil.com", None),
    ("https:%2F%2Fevil.com", None),
    ("a" * 41, None),
    ("t%C3%ADktok", None),                         # tíktok
    ("TI%E2%84%AATO%E2%84%AA", None),              # KELVIN SIGN lowercases to ASCII "k"
    ("%EF%BD%94%EF%BD%89%EF%BD%8B%EF%BD%94%EF%BD%8F%EF%BD%8B", None),  # fullwidth "tiktok"
    ("%F0%9F%9A%80", None),                        # emoji
    ("tiktok%0D%0ASet-Cookie:%20a=b", None),       # header-injection attempt
    ("tik%00tok", None),
    ("a/b", None),
    ("other", None),
]


#: Every state the smart link can be in. PRE-LAUNCH is the one live in production today (both
#: settings default to ""), so the hostile table must run there too — a change that reflected
#: an unknown campaign only when the destination is `/` (`/?c=<raw>`) would otherwise ship on
#: the passkey domain with every test green. MISCONFIGURED also lands on `/`, by a different road.
_LINK_CONFIGS = ("prelaunch", "launched", "launched_token", "misconfigured")


@pytest.fixture(params=_LINK_CONFIGS)
def link_config(request, monkeypatch):
    cfg = request.param
    if cfg in ("launched", "launched_token"):
        monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", _STORE)
    elif cfg == "misconfigured":
        monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", "https://evil.com/app/id6759525689")
    if cfg in ("launched_token", "misconfigured"):
        monkeypatch.setattr(settings, "MARKETING_APP_STORE_PROVIDER_TOKEN", _TOKEN)
    return cfg


def _expected_location(cfg, campaign):
    """The EXACT Location for a campaign that normalises to `campaign` (None = "other")."""
    if cfg in ("prelaunch", "misconfigured"):
        return "/"
    if cfg == "launched" or campaign is None:
        return _STORE
    return f"{_STORE}?pt={_TOKEN}&ct={campaign}&mt=8"


@pytest.mark.parametrize("raw,expected", _HOSTILE)
def test_hostile_campaigns_never_reach_location(client, link_config, raw, expected):
    r = _go(client, f"/go/{raw}")
    decoded = unquote(raw)
    if r.status_code == 404:
        # A router miss (a segment carrying "/") is fine — as long as nothing is reflected.
        # Configuration-independent by design.
        assert "location" not in {k.lower() for k in r.headers}
        assert decoded not in r.text and raw not in r.text
        assert expected is None
        return
    _assert_plain_302(r)
    loc = r.headers["location"]
    assert decoded not in loc and raw not in loc, loc
    # EXACT equality, never a substring or prefix check: a reflected, RE-ENCODED segment
    # (`/?c=https%3A//evil.com`) contains neither the raw nor the decoded form, and
    # `startswith("/")` passes any reflection at all.
    assert loc == _expected_location(link_config, expected), (link_config, loc)


@pytest.mark.parametrize("raw,expected", [
    (None, "other"), (123, "other"), ("", "other"), ("   ", "other"),
    ("tiktok", "tiktok"), ("TIKTOK", "tiktok"), (" x\n", "x"), ("tik tok", "other"),
    ("other", "other"), ("a" * 40, "other"), ("a" * 41, "other"), ("a" * 10_000, "other"),
    ("ｔｉｋｔｏｋ", "other"), ("TIKTOK", "other"), ("tik​tok", "other"),
    ("you-tube", "other"), ("devto", "devto"), ("x/../tiktok", "other"),
])
def test_normalize_campaign_table(raw, expected):
    assert smart_link.normalize_campaign(raw) == expected


def test_known_campaigns_are_exactly_the_post_platforms_and_fit_the_db_check():
    assert smart_link.KNOWN_CAMPAIGNS == frozenset(POST_PLATFORMS)
    # marketing_link_hits.campaign CHECK '^[a-z0-9_-]{1,40}$' — "other" included.
    for c in smart_link.KNOWN_CAMPAIGNS | {smart_link.OTHER}:
        assert re.fullmatch(r"[a-z0-9_-]{1,40}", c), c
    assert smart_link.OTHER not in smart_link.KNOWN_CAMPAIGNS


# ── 3. misconfigured store URL ────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [
    "http://apps.apple.com/app/id6759525689",
    "https://evil.com/app/id6759525689",
    "https://apps.apple.com.evil.com/app/id6759525689",
    "https://evil.com@apps.apple.com/app/id6759525689",
    "https://apps.apple.com:8443/app/id6759525689",
    "https://apps.apple.com\\@evil.com/",
    "https://apps.apple.com/app/id6759525689\r\nSet-Cookie: a=b",
    "https://apps.apple.com/app/id6759525689\"><script>",
    "//apps.apple.com/app/id6759525689",
    "javascript:alert(1)",
    "apps.apple.com/app/id6759525689",
    "https://apps.apple.com:abc/",
    "https://аpps.apple.com/app/id1",              # Cyrillic "а"
])
def test_a_misconfigured_store_url_falls_back_and_logs_error_once(
        client, monkeypatch, caplog, token, bad):
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", bad)
    caplog.set_level(logging.ERROR, logger=_LOGGER)
    for _ in range(3):
        r = _go(client, "/go/tiktok")
        _assert_plain_302(r)
        assert r.headers["location"] == "/"
    page = client.get("/")
    assert page.status_code == 200
    assert "Coming soon to the App Store" in page.text
    assert "apple-itunes-app" not in page.text
    errors = [rec for rec in caplog.records
              if rec.name == _LOGGER and rec.levelno == logging.ERROR
              and "MARKETING_APP_STORE_URL is misconfigured" in rec.getMessage()]
    assert len(errors) == 1, [rec.getMessage() for rec in errors]
    # The misconfigured value itself is never echoed into the log.
    assert "evil" not in errors[0].getMessage()


def test_an_empty_store_url_is_prelaunch_not_an_error(client, caplog):
    caplog.set_level(logging.ERROR, logger=_LOGGER)
    assert _go(client, "/go/tiktok").headers["location"] == "/"
    assert smart_link.store_url() is None and smart_link.store_app_id() is None
    assert not [r for r in caplog.records if r.name == _LOGGER]


def test_store_app_id_parses_the_numeric_id_only(monkeypatch):
    for url, want in [
        (_STORE, "6759525689"),
        ("https://apps.apple.com/app/id6759525689?l=en", "6759525689"),
        ("https://apps.apple.com/app/caydex", None),
        ("https://apps.apple.com/app/idabc", None),
    ]:
        monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", url)
        assert smart_link.store_app_id() == want, url


# ── 4. what is counted ────────────────────────────────────────────────────────


def test_a_real_visit_is_counted_under_its_campaign_and_the_et_day(client, fixed_day):
    _go(client, "/go/tiktok")
    _go(client, "/go/tiktok", ip="198.51.100.4")
    _go(client, "/go")
    _go(client, "/go/newsletter")
    _go(client, "/go/TikTok")
    assert smart_link._pending == {("tiktok", _DAY): 3, ("other", _DAY): 2}


def test_today_et_is_the_eastern_calendar_day():
    before = datetime.now(ET).date().isoformat()
    got = smart_link._today_et()
    after = datetime.now(ET).date().isoformat()
    assert got in (before, after)


_BOT_UAS = [
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Twitterbot/1.0",
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient +http://www.linkedin.com)",
    "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "TelegramBot (like TwitterBot)",
    "WhatsApp/2.23.20.0 A",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_5) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/13.1.1 Safari/605.1.15 (Applebot/0.1; +http://www.apple.com/go/applebot)",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Pinterest/0.2 (+https://www.pinterest.com/bot.html)",
    "redditbot/1.0",
    "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
    "some-crawler/1.0", "MySpider", "LinkPreview/2.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/120.0",
    "curl/8.4.0", "Wget/1.21.4", "python-requests/2.31.0", "python-httpx/0.27.0",
    "", "   ",
    # Pinterest's crawler, by its OWN token (the bare platform name is its in-app browser).
    "Pinterestbot/1.0 (+http://www.pinterest.com/bot.html)",
    "Mozilla/5.0 (compatible; Pinterestbot/1.0; +http://www.pinterest.com/bot.html)",
    "Mozilla/5.0 (compatible; Bluesky Cardyb/1.1; +mailto:support@bsky.app)",
    # Mail image proxies: Mozilla-prefixed, so only their own token catches them.
    "Mozilla/5.0 (Windows NT 5.1; rv:11.0) Gecko Firefox/11.0 (via ggpht.com GoogleImageProxy)",
    "YahooMailProxy; https://help.yahoo.com/kb/yahoo-mail-proxy-SLN28749.html",
    # Native HTTP stacks (client-side unfurlers in chat apps): no "bot", no browser prefix.
    "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/AP1A.240405.002)",
    "Slack/24.03.10 CFNetwork/1494.0.7 Darwin/23.4.0",
    "Microsoft Office/16.0 (Windows NT 10.0; Microsoft Outlook 16.0.17531; Pro)",
    "http.rb/5.1.1 (Mastodon/4.2.8; +https://mastodon.social/)",
]
#: Bots that ONLY the browser-prefix rule catches (the deny-list has no token for them).
_NATIVE_STACK_UAS = [
    "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/AP1A.240405.002)",
    "Slack/24.03.10 CFNetwork/1494.0.7 Darwin/23.4.0",
    "Microsoft Office/16.0 (Windows NT 10.0; Microsoft Outlook 16.0.17531; Pro)",
]
_HUMAN_UAS = [
    _BROWSER,
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    # In-app browsers are PEOPLE who tapped the link — never confuse them with the crawlers.
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
    "Gecko) Mobile/15E148 Instagram 330.0.3.12.235 (iPhone15,2; iOS 17_4; en_US)",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
    "Gecko) Mobile/15E148 [FBAN/FBIOS;FBAV/450.0.0.38.108]",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
    "Gecko) Mobile/15E148 musical_ly_33.8.0 JsSdk/2.0 NetType/WIFI",
    # Pinterest's in-app browser: people who tapped a pin, NOT its crawler.
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
    "Gecko) Mobile/15E148 [Pinterest/iOS]",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP1A.240405.002; wv) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/124.0.6367.82 Mobile Safari/537.36 "
    "[Pinterest/Android]",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
    "Gecko) Mobile/15E148 [LinkedInApp]/9.29.8963",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
    "Gecko) Mobile/15E148 Barcelona 330.0.0.21.109 (iPhone15,2; iOS 17_4; en_US; en; "
    "scale=3.00; 1179x2556)",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP1A.240405.002; wv) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/124.0.6367.82 Mobile Safari/537.36 TwitterAndroid",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
    "Gecko) Mobile/15E148 Snapchat/13.0.0.33 (like Safari/8618.1.15.10.15, panda)",
    # The one mainstream browser whose UA does not open with "Mozilla/".
    "Opera/9.80 (Android; Opera Mini/36.2.2254/119.132; U; id) Presto/2.12.423 Version/12.16",
]


@pytest.mark.parametrize("ua", _BOT_UAS)
def test_crawlers_are_redirected_but_not_counted(client, fixed_day, ua):
    assert smart_link.is_bot(ua)
    r = _go(client, "/go/tiktok", ua=ua)
    _assert_plain_302(r)
    assert smart_link._pending == {}


@pytest.mark.parametrize("ua", _HUMAN_UAS)
def test_browsers_and_in_app_browsers_are_people(client, fixed_day, ua):
    assert not smart_link.is_bot(ua)
    # And through the real route: a tap from each is counted.
    _assert_plain_302(_go(client, "/go/pinterest", ua=ua))
    assert smart_link._pending == {("pinterest", _DAY): 1}


@pytest.mark.parametrize("ua", _NATIVE_STACK_UAS)
def test_native_http_stacks_are_caught_by_the_browser_prefix_not_the_deny_list(ua):
    # Anti-vacuity for the prefix rule: the deny-list alone does NOT catch these.
    assert smart_link._BOT_UA_RE.search(ua) is None
    assert smart_link.is_bot(ua)


# ── 4b. only a navigation is a person ─────────────────────────────────────────

#: What a browser sends when the link is used as something OTHER than a link: an `<img>` in a
#: forum post or an HTML newsletter, a `fetch()` in any mode, an iframe (old Chrome said
#: `nested-navigate`). Page script cannot forge Sec-Fetch-*.
_NOT_NAVIGATIONS = [
    {"Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image", "Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "empty"},
    {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"},
    {"Sec-Fetch-Mode": "nested-navigate", "Sec-Fetch-Dest": "iframe"},
    {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "embed"},
    {"Sec-Fetch-Dest": "script"},
    {"Sec-Fetch-Mode": "no-cors"},
    {"Sec-Fetch-Mode": "same-origin", "Sec-Fetch-Dest": "document"},
]
_NAVIGATIONS = [
    # NO fetch metadata at all — WKWebView before iOS 16.4, several embedded browsers. Absence
    # is not evidence; requiring the headers would stop counting real taps.
    {},
    {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "cross-site",
     "Sec-Fetch-User": "?1"},
    {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"},
    {"Sec-Fetch-Mode": "NAVIGATE", "Sec-Fetch-Dest": "Document"},
    {"Sec-Fetch-Mode": "navigate"},
    {"Sec-Fetch-Dest": "document"},
]


@pytest.mark.parametrize("headers", _NOT_NAVIGATIONS)
def test_a_subresource_or_iframe_load_is_redirected_but_not_counted(client, fixed_day, headers):
    assert smart_link.is_non_navigation(headers)
    # A real browser UA from a fresh address: only the fetch metadata can refuse it.
    _assert_plain_302(_go(client, "/go/linkedin", headers=headers))
    assert smart_link._pending == {}


@pytest.mark.parametrize("headers", _NAVIGATIONS)
def test_a_navigation_or_a_client_without_fetch_metadata_is_counted(client, fixed_day, headers):
    assert not smart_link.is_non_navigation(headers)
    _assert_plain_302(_go(client, "/go/linkedin", headers=headers))
    assert smart_link._pending == {("linkedin", _DAY): 1}


def test_is_non_navigation_normalises_and_caps_values():
    assert not smart_link.is_non_navigation({"sec-fetch-mode": " Navigate ",
                                             "sec-fetch-dest": "DOCUMENT\t"})
    # Present but EMPTY is not something a browser sends: not a navigation.
    assert smart_link.is_non_navigation({"sec-fetch-mode": ""})
    # An oversized value is compared on its capped prefix and never matches a token.
    assert smart_link.is_non_navigation({"sec-fetch-dest": "document" + " x" * 50_000})


def test_an_image_embed_across_many_readers_counts_nobody(client, fixed_day):
    # The reported case: a forum post / HTML newsletter embeds /go/linkedin as an image and
    # every reader's own browser loads it from its own address.
    img = {"Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image",
           "Sec-Fetch-Site": "cross-site", "Accept": "image/avif,image/webp,*/*"}
    for i in range(30):
        _assert_plain_302(_go(client, "/go/linkedin", ip=f"198.51.100.{i + 1}", headers=img))
    assert smart_link._pending == {}


# ── 4c. the per-campaign ceilings ─────────────────────────────────────────────

#: A fixed wall clock for the window stamps in the ceiling summaries.
_WALL = 1_790_000_000.0
_WALL_START = "2026-09-21T14:13:20Z"
_WALL_END = "2026-09-21T14:14:20Z"


@pytest.fixture
def clock(monkeypatch):
    """Injected monotonic + wall clocks: advance `clock["t"]` to move both."""
    c = {"t": 1_000.0}
    monkeypatch.setattr(smart_link, "_monotonic", lambda: c["t"])
    monkeypatch.setattr(smart_link, "_wall_clock", lambda: _WALL + (c["t"] - 1_000.0))
    return c


def _req(ip, ua=_BROWSER):
    """A minimal request for `record_hit` — the bulk tests below skip the HTTP stack."""
    return SimpleNamespace(method="GET", headers={"user-agent": ua, "x-forwarded-for": ip},
                           client=SimpleNamespace(host=ip))


def _summaries(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == _LOGGER and "were redirected but NOT counted" in r.getMessage()]


def _engaged(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == _LOGGER and "reached its ceiling" in r.getMessage()]


def test_a_campaign_ceiling_bounds_counted_hits_from_an_address_pool(
        client, fixed_day, monkeypatch, caplog, clock):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    monkeypatch.setattr(smart_link, "_CAMPAIGN_RATE_MAX", 5)
    for i in range(8):  # eight distinct addresses, each well under its own budget
        _assert_plain_302(_go(client, "/go/tiktok", ip=f"198.51.100.{i + 1}"))
    assert smart_link._pending == {("tiktok", _DAY): 5}
    # The per-address limiter lives in the link's PRIVATE pool: nothing /go does — counted or
    # refused — ever inserts a key into the process-wide pools chat/report/auth share.
    assert not any(k.startswith("go:") for k in rate_limiter._requests)
    assert not any(k.startswith("go:") for k in rate_limiter._protected)
    engaged = _engaged(caplog)
    assert len(engaged) == 1, "one WARNING per window, not one per refused hit"
    assert "campaign=tiktok" in engaged[0] and _WALL_START in engaged[0], engaged

    # A WINDOW, not a latch: the next window counts again and reports what the last refused.
    clock["t"] += smart_link._CEILING_WINDOW_SECONDS
    _assert_plain_302(_go(client, "/go/tiktok", ip="198.51.100.50"))
    assert smart_link._pending == {("tiktok", _DAY): 6}
    summary = _summaries(caplog)
    assert len(summary) == 1, summary
    assert "campaign=tiktok" in summary[0] and " 3 hit(s)" in summary[0], summary
    assert f"window {_WALL_START}..{_WALL_END}" in summary[0], summary


def _fill_then_flood_from_one_address(n_flood):
    """The ceiling (5) fills from five addresses; ONE fresh address then sends `n_flood` taps
    in the same window. Returns what the window counted."""
    for i in range(5):
        assert smart_link.record_hit(_req(f"198.51.100.{i + 1}"), "tiktok")
    for _ in range(n_flood):
        smart_link.record_hit(_req("203.0.113.77"), "tiktok")
    return smart_link._windows["tiktok"].counted


def test_the_ceiling_summary_counts_only_what_raising_the_ceiling_would_recover(
        monkeypatch, caplog, clock, fixed_day):
    """W2L-2: after the ceiling filled, one address sent 500 taps. At most _RATE_MAX of them
    were ever countable — the per-address limit refuses the rest with or without a ceiling —
    so a summary saying 500 were lost to the ceiling (and "raise it") steered an operator into
    a deploy that recovers 20. The number reported must be the number raising it recovers."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    monkeypatch.setattr(smart_link, "_CAMPAIGN_RATE_MAX", 5)
    assert _fill_then_flood_from_one_address(500) == 5
    clock["t"] += smart_link._CEILING_WINDOW_SECONDS
    smart_link.report_ended_windows()
    summary = _summaries(caplog)
    assert len(summary) == 1 and f" {smart_link._RATE_MAX} hit(s)" in summary[0], summary

    # The same traffic with the ceiling out of the way: exactly that many more are counted.
    smart_link._windows.clear()
    smart_link._pending.clear()
    smart_link._link_limiter.clear()
    monkeypatch.setattr(smart_link, "_CAMPAIGN_RATE_MAX", 10**9)
    assert _fill_then_flood_from_one_address(500) == 5 + smart_link._RATE_MAX


@pytest.mark.asyncio
async def test_an_ended_window_is_reported_by_the_flush_loop_without_waiting_for_a_hit(
        monkeypatch, caplog, clock, fixed_day):
    """The summary used to be logged only when the campaign's NEXT qualifying hit opened a new
    window — hours after the flood, or never. The flush loop reports it within one cycle, and
    a reported window is never reported again."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    monkeypatch.setattr(smart_link, "_CAMPAIGN_RATE_MAX", 1)
    smart_link.record_hit(_req("198.51.100.1"), "youtube")
    smart_link.record_hit(_req("198.51.100.2"), "youtube")        # refused by the ceiling
    smart_link.report_ended_windows()
    assert _summaries(caplog) == [], "the window is still open"
    clock["t"] += smart_link._CEILING_WINDOW_SECONDS

    async def _noop_flush():
        return 0

    monkeypatch.setattr(smart_link, "flush_hits", _noop_flush)
    monkeypatch.setattr(smart_link, "_FLUSH_INTERVAL_SECONDS", 0.001)
    task = asyncio.create_task(smart_link.run_link_hit_flush_loop())
    try:
        await _until(lambda: _summaries(caplog))
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    summary = _summaries(caplog)
    assert len(summary) == 1, summary
    assert "campaign=youtube" in summary[0] and " 1 hit(s)" in summary[0]
    assert f"window {_WALL_START}..{_WALL_END}" in summary[0] and "partial" not in summary[0]
    # The next hit opens a fresh window without logging the old one a second time.
    smart_link.record_hit(_req("198.51.100.3"), "youtube")
    assert len(_summaries(caplog)) == 1


@pytest.mark.asyncio
async def test_shutdown_reports_a_window_still_open_as_partial(monkeypatch, caplog, clock,
                                                              fixed_day):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    monkeypatch.setattr(smart_link, "_CAMPAIGN_RATE_MAX", 1)
    for i in range(3):
        smart_link.record_hit(_req(f"198.51.100.{i + 1}"), "x")

    async def _noop_flush():
        return 0

    monkeypatch.setattr(smart_link, "flush_hits", _noop_flush)
    task = asyncio.create_task(smart_link.run_link_hit_flush_loop())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    summary = _summaries(caplog)
    assert len(summary) == 1 and "campaign=x" in summary[0] and " 2 hit(s)" in summary[0]
    assert "partial" in summary[0], summary


#: 40 /64s inside ONE /56 (2001:db8::/56) — a single home subscriber holds 256 of them.
_ONE_SLASH_56 = [f"2001:db8:0:{i:x}::1" for i in range(40)]


@pytest.mark.parametrize("flooded,slug", [("wp-login", "other"), ("instagram", "instagram")])
def test_a_flood_of_one_campaign_never_suppresses_another(fixed_day, clock, flooded, slug):
    """W2L-3: the ceiling used to be ONE budget for every campaign, spent by junk `/go/<slug>`
    traffic normalised to "other" too — so a script cycling 30 /64s of one /56 filled it and
    every real TikTok tap in that minute was redirected but not counted while the junk was.
    Exempting only "other" would not have helped: the same flood aimed at /go/instagram did
    the same. Each campaign has its own ceiling now, and the flood saturates only its target."""
    assert len({smart_link.rate_key(ip) for ip in _ONE_SLASH_56}) == len(_ONE_SLASH_56)
    import ipaddress as _ip
    assert all(_ip.ip_address(ip) in _ip.ip_network("2001:db8::/56") for ip in _ONE_SLASH_56)
    campaign = smart_link.normalize_campaign(flooded)
    assert campaign == slug
    for ip in _ONE_SLASH_56:
        for _ in range(smart_link._RATE_MAX + 5):
            smart_link.record_hit(_req(ip), campaign)
    ceiling = smart_link._ceiling_for(slug)
    assert smart_link._pending == {(slug, _DAY): ceiling}, "the flood fills its own ceiling"
    assert smart_link._windows[slug].refused > 0, "sentinel: the flood did reach the ceiling"
    for i in range(50):
        assert smart_link.record_hit(_req(f"198.51.100.{i + 1}"), "tiktok") is True
    assert smart_link._pending == {(slug, _DAY): ceiling, ("tiktok", _DAY): 50}


def test_other_has_its_own_smaller_ceiling(client, fixed_day, clock):
    """Nothing links to "other" (post_copy.cta_for only emits the known platforms), so junk
    slugs, typos and bare /go get a tenth of a campaign's budget."""
    for i in range(10):
        for path in ("/go/wp-login", "/go"):
            for _ in range(smart_link._RATE_MAX // 2 + 1):
                _go(client, path, ip=f"198.51.100.{i + 1}")
    assert smart_link._pending == {("other", _DAY): smart_link._OTHER_RATE_MAX}


def test_the_other_ceiling_summary_names_the_constant_that_caps_other(caplog, clock, fixed_day):
    """W3L-4: "other" is capped by _OTHER_RATE_MAX, but its summary told the operator to raise
    _CAMPAIGN_RATE_MAX — which recovers nothing for it (the reporter raised it to 1e6 and
    "other" still counted 60). Real defaults: 70 junk-slug taps from 70 addresses."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    for i in range(smart_link._OTHER_RATE_MAX + 10):
        smart_link.record_hit(_req(f"10.0.{i // 250}.{i % 250 + 1}"), "wp-login")
    assert smart_link._windows["other"].counted == smart_link._OTHER_RATE_MAX
    clock["t"] += smart_link._CEILING_WINDOW_SECONDS
    smart_link.report_ended_windows()
    summary = _summaries(caplog)
    assert len(summary) == 1 and "campaign=other" in summary[0] and " 10 hit(s)" in summary[0]
    assert "_OTHER_RATE_MAX" in summary[0], summary
    assert "_CAMPAIGN_RATE_MAX" not in summary[0], summary
    assert "if it was a real spike" not in summary[0], "no post links to 'other'"


def test_a_known_campaign_summary_still_names_the_campaign_ceiling(
        monkeypatch, caplog, clock, fixed_day):
    """The twin: a real campaign's summary keeps naming _CAMPAIGN_RATE_MAX, never 'other''s."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    monkeypatch.setattr(smart_link, "_CAMPAIGN_RATE_MAX", 5)
    for i in range(8):
        smart_link.record_hit(_req(f"198.51.100.{i + 1}"), "tiktok")
    clock["t"] += smart_link._CEILING_WINDOW_SECONDS
    smart_link.report_ended_windows()
    summary = _summaries(caplog)
    assert len(summary) == 1 and "campaign=tiktok" in summary[0], summary
    assert "raising _CAMPAIGN_RATE_MAX would have recovered" in summary[0], summary
    assert "_OTHER_RATE_MAX" not in summary[0], summary


def test_hits_the_per_address_limiter_refuses_do_not_spend_the_campaign_budget(
        client, fixed_day, monkeypatch, clock):
    monkeypatch.setattr(smart_link, "_CAMPAIGN_RATE_MAX", smart_link._RATE_MAX + 5)
    for _ in range(smart_link._RATE_MAX * 2):  # one address: half counted, half refused
        _go(client, "/go/x", ip="203.0.113.9")
    assert smart_link._windows["x"].counted == smart_link._RATE_MAX
    assert smart_link._windows["x"].refused == 0, "the address's own limit refused those"
    for i in range(5):
        _go(client, "/go/x", ip=f"203.0.113.{100 + i}")
    assert smart_link._pending == {("x", _DAY): smart_link._RATE_MAX + 5}
    _go(client, "/go/x", ip="203.0.113.200")  # the ceiling is now full
    assert smart_link._pending == {("x", _DAY): smart_link._RATE_MAX + 5}
    assert smart_link._windows["x"].refused == 1


def test_the_default_ceilings_are_far_above_the_per_address_budget():
    # Guards the constants from being "tightened" into something a single real spike hits.
    assert smart_link._CAMPAIGN_RATE_MAX >= 30 * smart_link._RATE_MAX
    # "other" takes at least one whole address, and less than a real campaign.
    assert smart_link._RATE_MAX <= smart_link._OTHER_RATE_MAX < smart_link._CAMPAIGN_RATE_MAX
    assert smart_link._CEILING_WINDOW_SECONDS == smart_link._RATE_WINDOW_SECONDS
    assert smart_link._ceiling_for("other") == smart_link._OTHER_RATE_MAX
    assert all(smart_link._ceiling_for(c) == smart_link._CAMPAIGN_RATE_MAX
               for c in smart_link.KNOWN_CAMPAIGNS)


def test_is_bot_treats_a_missing_ua_as_a_bot_and_is_linear():
    assert smart_link.is_bot(None)
    started = time.perf_counter()
    assert not smart_link.is_bot("Mozilla/5.0 " + "a" * 200_000)
    assert time.perf_counter() - started < 0.1


def test_head_is_redirected_but_not_counted(client, fixed_day):
    _assert_plain_302(_go(client, "/go/tiktok", method="HEAD"))
    assert smart_link._pending == {}


@pytest.mark.parametrize("headers", [
    {"Sec-Purpose": "prefetch"},
    {"Sec-Purpose": "prefetch;prerender"},
    {"Purpose": "prefetch"},
    {"X-Moz": "prefetch"},
    {"sec-purpose": "PREFETCH"},
])
def test_prefetches_are_redirected_but_not_counted(client, fixed_day, headers):
    assert smart_link.is_prefetch(headers)
    _assert_plain_302(_go(client, "/go/tiktok", headers=headers))
    assert smart_link._pending == {}


def test_is_prefetch_ignores_ordinary_requests():
    assert not smart_link.is_prefetch({})
    assert not smart_link.is_prefetch({"X-Moz": "something-else", "Accept": "text/html"})


def test_rate_limited_callers_are_redirected_but_not_counted(client, fixed_day):
    for i in range(25):
        # Rotating the LEFTMOST X-Forwarded-For entry (caller-controlled) buys nothing: the
        # limiter keys on the rightmost, which our edge appends.
        r = _go(client, "/go/tiktok", ip=f"10.0.0.{i}, 203.0.113.50")
        _assert_plain_302(r)
    assert smart_link._pending == {("tiktok", _DAY): smart_link._RATE_MAX}
    # A different address has its own budget.
    _go(client, "/go/tiktok", ip="203.0.113.51")
    assert smart_link._pending == {("tiktok", _DAY): smart_link._RATE_MAX + 1}


def test_ipv6_addresses_in_one_slash_64_share_one_budget(client, fixed_day):
    for i in range(25):
        _assert_plain_302(_go(client, "/go/x", ip=f"2001:db8:abcd:12::{i + 1:x}"))
    assert smart_link._pending == {("x", _DAY): smart_link._RATE_MAX}
    _go(client, "/go/x", ip="2001:db8:abcd:13::1")
    assert smart_link._pending == {("x", _DAY): smart_link._RATE_MAX + 1}


def test_the_link_limiter_is_private_to_the_smart_link(client, fixed_day):
    """The per-address check runs BEFORE the campaign ceiling (so the ceiling's refused count
    is exactly what raising it would recover), which is only safe because its keys live in the
    link's own pool: a flood can never insert `go:ip:` keys among the chat/report/analytics
    buckets, nor anywhere near the credential pool."""
    from app.core.security import RateLimiter

    _go(client, "/go/tiktok", ip="203.0.113.60")
    assert "go:ip:203.0.113.60" in smart_link._link_limiter._requests
    assert smart_link._link_limiter is not rate_limiter
    assert isinstance(smart_link._link_limiter, RateLimiter)
    assert not any(k.startswith("go:") for k in rate_limiter._requests)
    assert not any(k.startswith("go:") for k in rate_limiter._protected)
    # Strictly smaller than the shared pool's: a renamed/inert override would inherit 20k.
    assert smart_link._link_limiter._MAX_TRACKED < RateLimiter._MAX_TRACKED
    # The smaller cap is honoured by the eviction itself, not just declared.
    lim = smart_link._LinkLimiter()
    for i in range(lim._MAX_TRACKED + 10):
        assert lim.is_allowed(f"go:ip:{i}", smart_link._RATE_MAX, smart_link._RATE_WINDOW_SECONDS)
    assert len(lim._requests) == lim._MAX_TRACKED
    assert f"go:ip:{lim._MAX_TRACKED + 9}" in lim._requests, "the caller is never evicted"


@pytest.mark.parametrize("ip,key", [
    ("203.0.113.7", "203.0.113.7"),
    (" 203.0.113.7 ", "203.0.113.7"),
    ("2001:db8:abcd:12:1:2:3:4", "2001:db8:abcd:12::/64"),
    ("2001:DB8:ABCD:12::FFFF", "2001:db8:abcd:12::/64"),
    ("::ffff:198.51.100.9", "198.51.100.9"),
    ("garbage", "unknown"), ("", "unknown"), (None, "unknown"), ("999.1.1.1", "unknown"),
    ("1.2.3.4/24", "unknown"), ("testclient", "unknown"), ("1" * 10_000, "unknown"),
])
def test_rate_key_table(ip, key):
    assert smart_link.rate_key(ip) == key


def test_record_hit_never_raises(caplog):
    class _Broken:
        method = "GET"

        @property
        def headers(self):
            raise RuntimeError("boom")

    caplog.set_level(logging.WARNING, logger=_LOGGER)
    assert smart_link.record_hit(_Broken(), "tiktok") is False
    assert any("not recorded" in r.getMessage() for r in caplog.records if r.name == _LOGGER)


def test_record_hit_renormalises_a_raw_campaign(fixed_day):
    req = SimpleNamespace(method="GET", headers={"user-agent": _BROWSER},
                          client=SimpleNamespace(host="203.0.113.70"))
    assert smart_link.record_hit(req, "../evil") is True
    assert smart_link._pending == {("other", _DAY): 1}


def test_the_pending_dict_is_hard_capped(client, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    monkeypatch.setattr(smart_link, "_MAX_PENDING_KEYS", 3)
    days = iter(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05",
                 "2026-01-03"])
    monkeypatch.setattr(smart_link, "_today_et", lambda: next(days))
    for i in range(6):
        _assert_plain_302(_go(client, "/go/tiktok", ip=f"198.51.100.{i + 1}"))
    # Three keys admitted, two new keys dropped, an EXISTING key still increments when full.
    assert smart_link._pending == {("tiktok", "2026-01-01"): 1, ("tiktok", "2026-01-02"): 1,
                                   ("tiktok", "2026-01-03"): 2}
    warnings = [r for r in caplog.records if r.name == _LOGGER and "is full" in r.getMessage()]
    assert len(warnings) == 1


# ── 5. flush ──────────────────────────────────────────────────────────────────


class _FakeSupabase:
    """Records every RPC; `fail` maps (campaign, day) → exception to raise from execute()."""

    def __init__(self, fail=None, on_execute=None):
        self.calls = []
        self.fail = dict(fail or {})
        self.on_execute = on_execute

    def rpc(self, name, params):
        sb = self

        class _Call:
            def execute(self_inner):
                sb.calls.append((name, dict(params)))
                if sb.on_execute:
                    sb.on_execute(len(sb.calls))
                exc = sb.fail.get((params["p_campaign"], params["p_day"]))
                if exc is not None:
                    raise exc
                return SimpleNamespace(data=None)

        return _Call()


def _rpc(campaign, day, n):
    return ("increment_marketing_link_hits", {"p_campaign": campaign, "p_day": day, "p_count": n})


@pytest.mark.asyncio
async def test_flush_sends_one_rpc_per_key_with_the_right_params(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(smart_link, "get_supabase", lambda: fake)
    smart_link._pending.update({("x", _DAY): 1, ("tiktok", _DAY): 3, ("tiktok", "2026-09-22"): 2})
    assert await smart_link.flush_hits() == 6
    assert fake.calls == [_rpc("tiktok", "2026-09-22", 2), _rpc("tiktok", _DAY, 3),
                          _rpc("x", _DAY, 1)]
    assert smart_link._pending == {}


@pytest.mark.asyncio
async def test_an_empty_flush_touches_nothing(monkeypatch):
    def _never():
        raise AssertionError("get_supabase must not be called for an empty flush")

    monkeypatch.setattr(smart_link, "get_supabase", _never)
    assert await smart_link.flush_hits() == 0


@pytest.mark.parametrize("exc,pending,retry", [
    # A proven non-commit merges back with the new hits: one tier, nothing was counted.
    (httpx.ConnectError("[Errno 111] Connection refused"), 8, None),
    # An unknown outcome moves the SENT hits to the re-send tier; the hit that arrived
    # mid-flight is fresh and must not inherit their one remaining attempt.
    (RuntimeError("520 upstream"), 5, 3),
])
@pytest.mark.asyncio
async def test_a_failed_key_is_kept_apart_from_hits_that_arrived_mid_flush(
        monkeypatch, caplog, exc, pending, retry):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    k_fail = ("tiktok", _DAY)
    # A hit recorded WHILE the first RPC is in flight must land in the fresh dict and survive
    # the merge-back of the failed key (the swap happened before the first await).
    fake = _FakeSupabase(fail={k_fail: exc},
                         on_execute=lambda n: smart_link._add(k_fail, 5) if n == 1 else None)
    monkeypatch.setattr(smart_link, "get_supabase", lambda: fake)
    smart_link._pending.update({k_fail: 3, ("x", _DAY): 2})
    assert await smart_link.flush_hits() == 2
    assert smart_link._pending == {k_fail: pending}
    assert smart_link._retry == ({k_fail: retry} if retry else {})
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR and r.name == _LOGGER]
    assert any(r.levelno == logging.WARNING and "failed and were kept" in r.getMessage()
               for r in caplog.records if r.name == _LOGGER)


@pytest.mark.parametrize("make_exc", [
    lambda: APIError({"message": "Could not find the function "
                      "public.increment_marketing_link_hits(p_campaign, p_count, p_day) in the "
                      "schema cache", "code": "PGRST202", "hint": None, "details": None}),
    lambda: APIError({"message": "relation \"public.marketing_link_hits\" does not exist",
                      "code": "42P01", "hint": None, "details": None}),
    lambda: RuntimeError("function increment_marketing_link_hits(text, date, integer) does "
                         "not exist (SQLSTATE 42883)"),
    lambda: APIError({"message": "Could not find the table", "code": "PGRST205",
                      "hint": None, "details": None}),
])
@pytest.mark.asyncio
async def test_a_missing_rpc_logs_migration_173_not_applied_once(monkeypatch, caplog, make_exc):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    key = ("tiktok", _DAY)
    fake = _FakeSupabase(fail={key: make_exc(), ("x", _DAY): make_exc()})
    monkeypatch.setattr(smart_link, "get_supabase", lambda: fake)
    smart_link._pending.update({key: 4, ("x", _DAY): 1})
    for _ in range(3):
        assert await smart_link.flush_hits() == 0
    assert smart_link._pending == {key: 4, ("x", _DAY): 1}, "counts must be kept, not lost"
    errors = [r for r in caplog.records if r.name == _LOGGER and r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "migration 173 not applied" in errors[0].getMessage()
    assert len(fake.calls) == 6


@pytest.mark.asyncio
async def test_merge_back_respects_the_key_cap(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    monkeypatch.setattr(smart_link, "_MAX_PENDING_KEYS", 1)
    boom = RuntimeError("down")
    fake = _FakeSupabase(fail={("tiktok", _DAY): boom, ("x", _DAY): boom})
    monkeypatch.setattr(smart_link, "get_supabase", lambda: fake)
    smart_link._pending.update({("tiktok", _DAY): 1, ("x", _DAY): 1})  # built past the cap
    assert await smart_link.flush_hits() == 0
    # Both tiers count toward ONE cap (an unknown outcome parks its hits in `_retry`).
    assert len(smart_link._pending) + len(smart_link._retry) == 1
    assert any("is full" in r.getMessage() for r in caplog.records if r.name == _LOGGER)


def test_the_key_cap_counts_both_tiers(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    monkeypatch.setattr(smart_link, "_MAX_PENDING_KEYS", 2)
    k1, k2, k3 = ("tiktok", _DAY), ("x", _DAY), ("youtube", _DAY)
    assert smart_link._add_retry(k1, 1) and smart_link._add(k2, 1)
    assert smart_link._add(k3, 1) is False and smart_link._add_retry(k3, 1) is False
    # An existing key in its own tier still increments when the cap is full.
    assert smart_link._add(k2, 1) and smart_link._add_retry(k1, 1)
    assert (smart_link._pending, smart_link._retry) == ({k2: 2}, {k1: 2})
    assert len([r for r in caplog.records if "is full" in r.getMessage()]) == 1


@pytest.mark.asyncio
async def test_a_count_above_the_rpc_ceiling_is_split_across_flushes(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(smart_link, "get_supabase", lambda: fake)
    smart_link._pending[("tiktok", _DAY)] = 1_000_005
    assert await smart_link.flush_hits() == 1_000_000
    assert smart_link._pending == {("tiktok", _DAY): 5}
    assert await smart_link.flush_hits() == 5
    assert [c[1]["p_count"] for c in fake.calls] == [1_000_000, 5]


@pytest.mark.asyncio
async def test_no_supabase_client_keeps_every_count(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    def _down():
        raise RuntimeError("SUPABASE_URL unset")

    monkeypatch.setattr(smart_link, "get_supabase", _down)
    smart_link._pending.update({("tiktok", _DAY): 2})
    smart_link._retry.update({("tiktok", _DAY): 7, ("x", _DAY): 1})
    assert await smart_link.flush_hits() == 0
    assert smart_link._pending == {("tiktok", _DAY): 2}
    assert smart_link._retry == {("tiktok", _DAY): 7, ("x", _DAY): 1}, "tiers kept apart"


class _Ledger:
    """A fake `increment_marketing_link_hits` that keeps the DATABASE's totals, so a test can
    assert what was actually counted, not just what was sent. Thread-safe: `execute()` runs in
    the real worker thread `sb_exec` uses.

    `raise_after_commit` / `raise_before_commit`: exception raised on a campaign's FIRST call,
    after / before the increment is applied. `block`: that campaign's FIRST call waits on
    `release` (bounded) after signalling `entered`, then commits — like a cancelled `to_thread`
    whose RPC keeps running. `script`: campaign → one action per call, consumed in order —
    `None` (succeed), `("before", exc)` or `("after", exc)`; an exhausted script succeeds.
    `sent` records every `p_count` per campaign.
    """

    def __init__(self, raise_after_commit=None, raise_before_commit=None, block=None,
                 script=None):
        self.raise_after_commit = dict(raise_after_commit or {})
        self.raise_before_commit = dict(raise_before_commit or {})
        self.script = {c: list(steps) for c, steps in (script or {}).items()}
        self.block = block
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.calls = []
        self.sent = {}
        self.finished = 0
        self.db = {}

    def rpc(self, name, params):
        ledger = self
        assert name == "increment_marketing_link_hits"

        class _Call:
            def execute(self_inner):
                c, n = params["p_campaign"], params["p_count"]
                # The RPC's own CHECK: p_count in [1, 1e6] (22023 otherwise).
                assert 1 <= n <= smart_link._MAX_RPC_COUNT, n
                with ledger.lock:
                    first = c not in ledger.calls
                    ledger.calls.append(c)
                    ledger.sent.setdefault(c, []).append(n)
                    steps = ledger.script.get(c)
                    step = steps.pop(0) if steps else None
                try:
                    if step is not None and step[0] == "before":
                        raise step[1]
                    if step is not None:
                        assert step[0] == "after", step
                        with ledger.lock:
                            ledger.db[c] = ledger.db.get(c, 0) + n
                        raise step[1]
                    if first and c in ledger.raise_before_commit:
                        raise ledger.raise_before_commit[c]
                    if first and c == ledger.block:
                        ledger.entered.set()
                        ledger.release.wait(5)
                    with ledger.lock:
                        ledger.db[c] = ledger.db.get(c, 0) + n
                    if first and c in ledger.raise_after_commit:
                        raise ledger.raise_after_commit[c]
                    return SimpleNamespace(data=n)
                finally:
                    with ledger.lock:
                        ledger.finished += 1

        return _Call()


@pytest.mark.parametrize("make_exc", [
    lambda: httpx.ReadTimeout("The read operation timed out"),
    lambda: httpx.RemoteProtocolError("Server disconnected without sending a response."),
    # postgrest's torn-down response: the HTTP status arrives as an INT code.
    lambda: APIError({"message": "JSON could not be generated", "code": 524,
                      "hint": "Refer to full message for details", "details": "b''"}),
])
@pytest.mark.asyncio
async def test_a_lost_response_after_commit_is_re_sent_at_least_once_and_says_so(
        monkeypatch, caplog, make_exc):
    """The documented trade-off, pinned: the RPC has no batch id, so a batch that committed
    but whose response was lost is re-sent and COUNTS TWICE. What must never happen is that
    silently — the flush logs an UNKNOWN OUTCOME naming the campaign."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    ledger = _Ledger(raise_after_commit={"tiktok": make_exc()})
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    smart_link._pending[("tiktok", _DAY)] = 40
    assert await smart_link.flush_hits() == 0
    assert smart_link._pending == {}
    assert smart_link._retry == {("tiktok", _DAY): 40}, "kept for its ONE re-send"
    warned = [r.getMessage() for r in caplog.records
              if r.name == _LOGGER and r.levelno == logging.WARNING]
    assert any("UNKNOWN OUTCOME" in m and "may be counted twice" in m and "campaign=tiktok" in m
               for m in warned), warned
    assert await smart_link.flush_hits() == 40
    assert ledger.db == {"tiktok": 80}, "at-least-once: 40 real taps, 80 counted"


@pytest.mark.parametrize("make_exc", [
    lambda: httpx.ConnectError("[Errno 111] Connection refused"),
    lambda: httpx.ConnectTimeout("timed out"),
    lambda: httpx.PoolTimeout("no connection available"),
    lambda: APIError({"message": "p_count out of range", "code": "22023", "hint": None,
                      "details": None}),
])
@pytest.mark.asyncio
async def test_a_failure_before_commit_is_retried_exactly_once_and_not_flagged(
        monkeypatch, caplog, make_exc):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    ledger = _Ledger(raise_before_commit={"tiktok": make_exc()})
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    smart_link._pending[("tiktok", _DAY)] = 7
    assert await smart_link.flush_hits() == 0
    msgs = [r.getMessage() for r in caplog.records if r.name == _LOGGER]
    assert any("failed and were kept" in m for m in msgs), msgs
    assert not any("UNKNOWN OUTCOME" in m for m in msgs), msgs
    assert await smart_link.flush_hits() == 7
    assert ledger.db == {"tiktok": 7}


@pytest.mark.parametrize("exc,unsent", [
    (httpx.ConnectError("refused"), True),
    (httpx.ConnectTimeout("t"), True),
    (httpx.PoolTimeout("t"), True),
    (APIError({"message": "m", "code": "PGRST202", "hint": None, "details": None}), True),
    (APIError({"message": "m", "code": "42P01", "hint": None, "details": None}), True),
    (APIError({"message": "m", "code": "22023", "hint": None, "details": None}), True),
    (RuntimeError("function increment_marketing_link_hits does not exist (SQLSTATE 42883)"),
     True),
    (httpx.ReadTimeout("t"), False),
    (httpx.WriteTimeout("t"), False),
    (httpx.RemoteProtocolError("gone"), False),
    (APIError({"message": "JSON could not be generated", "code": 520, "hint": None,
               "details": None}), False),
    (APIError({"message": "m", "code": "520", "hint": None, "details": None}), False),
    (APIError({"message": "m", "code": None, "hint": None, "details": None}), False),
    (RuntimeError("520 upstream"), False),
    # W2L-5: a gateway page whose Ray ID happens to contain "42883". The int code is the whole
    # answer — an HTTP status, commit unknown — whatever the HTML in `details` says.
    (APIError({"message": "JSON could not be generated", "code": 524,
               "hint": "Refer to full message for details",
               "details": "b'<!DOCTYPE html><span>Ray ID: <strong>8c9d42883e1f0a7b</strong>"
                          "</span> PGRST202 42P01'"}), False),
    (APIError({"message": "Could not find the function PGRST202", "code": None, "hint": None,
               "details": None}), False),
    # Class 08: the connection died, the COMMIT may or may not have run.
    (APIError({"message": "connection failure", "code": "08006", "hint": None,
               "details": None}), False),
    (APIError({"message": "m", "code": " pgrst202 ", "hint": None, "details": None}), True),
    (RuntimeError("upstream page, ray 8c9d42883e1f0a7b"), False),
    # W3L-1: an HTTP status that PROVES the origin was never reached. postgrest gives every
    # non-PostgREST body an int code, and PostgREST's own 4xx carry a PGRST*/SQLSTATE string,
    # so an int 4xx is the gateway refusing before it forwarded; 521/522/523/525/526/530 are
    # Cloudflare's "never reached the origin" family.
    *[(APIError({"message": "JSON could not be generated", "code": s, "hint": None,
                 "details": None}), True)
      for s in (400, 401, 403, 404, 413, 429, 521, 522, 523, 525, 526, 530)],
    (APIError({"message": "m", "code": "429", "hint": None, "details": None}), True),
    # ... and its twins that must STAY unknown: 408/499 can be emitted for a request the origin
    # was already handling, and a 5xx outside that family can arrive after the commit (an int
    # 503/502 is the gateway's — PostgREST's own 503 carries PGRST00x).
    *[(APIError({"message": "JSON could not be generated", "code": s, "hint": None,
                 "details": None}), False)
      for s in (408, 499, 500, 502, 503, 504, 520, 524, 527, 598, 399, 200)],
    (APIError({"message": "m", "code": "503", "hint": None, "details": None}), False),
    (APIError({"message": "m", "code": True, "hint": None, "details": None}), False),
])
def test_only_a_proven_non_commit_is_classified_unsent(exc, unsent):
    assert smart_link._definitely_unsent(exc) is unsent


@pytest.mark.parametrize("exc,missing", [
    (APIError({"message": "m", "code": "PGRST202", "hint": None, "details": None}), True),
    (APIError({"message": "m", "code": "42883", "hint": None, "details": None}), True),
    (RuntimeError("function increment_marketing_link_hits does not exist (SQLSTATE 42883)"),
     True),
    (RuntimeError("relation \"marketing_link_hits\" does not exist: 42P01"), True),
    # A present code is the answer; the text around it is never scanned.
    (APIError({"message": "JSON could not be generated", "code": 524, "hint": None,
               "details": "b'Ray ID: <strong>8c9d42883e1f0a7b</strong>'"}), False),
    (APIError({"message": "p_count out of range: PGRST202", "code": "22023", "hint": None,
               "details": None}), False),
    # Without a code, only a whole token counts — never a run inside a hex id.
    (RuntimeError("ray 8c9d42883e1f0a7b"), False),
    (RuntimeError("xPGRST202"), False),
])
def test_looks_missing_reads_the_structured_code_first(exc, missing):
    assert smart_link._looks_missing(exc) is missing


@pytest.mark.asyncio
async def test_a_real_gateway_page_is_an_unknown_outcome_not_a_missing_migration(
        monkeypatch, caplog):
    """W2L-5 through the REAL postgrest 1.1.1 client: a 524 after the commit, whose HTML page
    carries a Ray ID containing "42883". It used to be classified "definitely unsent" by a
    substring scan of str(e) and to fire the one-shot ERROR "migration 173 not applied" on a
    database where it is applied (a false Sentry event), with the UNKNOWN OUTCOME label lost."""
    from postgrest import SyncPostgrestClient

    db = {"tiktok": 0}
    responses = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        params = json.loads(request.content)
        db[params["p_campaign"]] += params["p_count"]          # committed ...
        if not responses:
            responses.append("524")
            return httpx.Response(                             # ... then the edge times out
                524, headers={"content-type": "text/html"},
                content=b"<!DOCTYPE html><html><body>A timeout occurred. Ray ID: "
                        b"<strong>8c9d42883e1f0a7b</strong></body></html>")
        return httpx.Response(200, json=None)

    sb = SyncPostgrestClient("http://supabase.invalid/rest/v1",
                             http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(smart_link, "get_supabase", lambda: sb)
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    smart_link._pending[("tiktok", _DAY)] = 40
    assert await smart_link.flush_hits() == 0
    assert not [r for r in caplog.records if r.name == _LOGGER and r.levelno >= logging.ERROR]
    warned = [r.getMessage() for r in caplog.records if r.name == _LOGGER]
    assert any("UNKNOWN OUTCOME" in m and "APIError" in m for m in warned), warned
    assert smart_link._retry == {("tiktok", _DAY): 40} and smart_link._pending == {}
    assert await smart_link.flush_hits() == 40
    assert db == {"tiktok": 80}


#: Real response bodies for an outage in FRONT of PostgREST: nothing reached the database.
_PRE_ORIGIN_RESPONSES = {
    "cf522": (522, "text/html",
              b"<!DOCTYPE html><html><title>Connection timed out | Error code 522</title>"
              b"<body>Ray ID: <strong>8c9d42883e1f0a7b</strong></body></html>"),
    "cf521": (521, "text/html",
              b"<!DOCTYPE html><html><title>Web server is down | Error code 521</title></html>"),
    "cf523": (523, "text/html",
              b"<!DOCTYPE html><html><title>Origin is unreachable | Error code 523</title></html>"),
    # The gateway's own JSON — no `code`, so postgrest cannot parse it as a PostgREST error.
    "gw401": (401, "application/json",
              b'{"message":"Invalid API key","hint":"Double check your Supabase `anon` or '
              b'`service_role` API key."}'),
    "gw429": (429, "application/json", b'{"message":"Too many requests"}'),
}


def _outage_client(kind, db, state):
    """The REAL postgrest client over a mock transport. While `state["down"]`: a pre-origin
    `kind` answers its page WITHOUT touching `db`; "cf524_after_commit" commits, then times out
    at the edge (the genuinely ambiguous twin). Otherwise every call commits and answers 200."""
    import json

    from postgrest import SyncPostgrestClient

    def handler(request: httpx.Request) -> httpx.Response:
        params = json.loads(request.content)
        if state["down"]:
            if kind == "cf524_after_commit":
                db[params["p_campaign"]] = db.get(params["p_campaign"], 0) + params["p_count"]
                return httpx.Response(524, headers={"content-type": "text/html"},
                                      content=b"<!DOCTYPE html><html>A timeout occurred</html>")
            status, ctype, body = _PRE_ORIGIN_RESPONSES[kind]
            return httpx.Response(status, headers={"content-type": ctype}, content=body)
        db[params["p_campaign"]] = db.get(params["p_campaign"], 0) + params["p_count"]
        return httpx.Response(200, json=None)

    return SyncPostgrestClient("http://supabase.invalid/rest/v1",
                               http_client=httpx.Client(transport=httpx.MockTransport(handler)))


async def _five_minute_outage(monkeypatch, kind):
    """10 real taps a minute through a 5-flush outage, then recovery: 10 more taps and two
    flushes. Returns (real taps, database total)."""
    db, state = {}, {"down": True}
    sb = _outage_client(kind, db, state)
    monkeypatch.setattr(smart_link, "get_supabase", lambda: sb)
    key = ("tiktok", _DAY)
    real = 0
    for _ in range(5):
        smart_link._add(key, 10)
        real += 10
        assert await smart_link.flush_hits() == 0
    state["down"] = False
    smart_link._add(key, 10)
    real += 10
    await smart_link.flush_hits()
    await smart_link.flush_hits()
    assert (smart_link._pending, smart_link._retry) == ({}, {}), "nothing left behind"
    return real, db.get("tiktok", 0)


@pytest.mark.parametrize("kind", sorted(_PRE_ORIGIN_RESPONSES))
@pytest.mark.asyncio
async def test_an_outage_that_never_reached_the_origin_is_counted_exactly_once(
        monkeypatch, caplog, kind):
    """W3L-1: postgrest gives a Cloudflare 521/522/523 page or a gateway 401/429 JSON an INT
    code, and every int status used to mean "may have committed". After round 2's drop rule
    those hits were DROPPED on the second failing flush — a 5-minute edge outage stored 20 of
    60 taps while the log said they "may" have been counted. They provably were not: they are
    held and re-sent until they land, exactly once, and nothing claims an unknown outcome."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    status = _PRE_ORIGIN_RESPONSES[kind][0]
    # Sentinel: the real client really does hand this response over as an INT status code.
    probe = _outage_client(kind, {}, {"down": True})
    with pytest.raises(APIError) as raised:
        probe.rpc("increment_marketing_link_hits",
                  {"p_campaign": "tiktok", "p_day": _DAY, "p_count": 1}).execute()
    assert raised.value.code == status and isinstance(raised.value.code, int)

    real, counted = await _five_minute_outage(monkeypatch, kind)
    assert counted == real == 60
    msgs = [r.getMessage() for r in caplog.records if r.name == _LOGGER]
    assert not any("DROPPED" in m or "UNKNOWN OUTCOME" in m for m in msgs), msgs
    assert sum("failed and were kept" in m for m in msgs) == 5, msgs
    assert not [r for r in caplog.records if r.name == _LOGGER and r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_the_ambiguous_edge_timeout_twin_still_gets_the_at_most_twice_bound(
        monkeypatch, caplog):
    """The must-stay-bounded twin of the test above, through the same harness: a 524 AFTER the
    commit is a genuine unknown outcome, so it gets one re-send and a second unknown outcome
    drops it (W2L-1) — at most twice, never the unbounded re-send a pre-origin outage gets."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    real, counted = await _five_minute_outage(monkeypatch, "cf524_after_commit")
    assert (real, counted) == (60, 110)
    assert counted <= 2 * real
    dropped = [r.getMessage() for r in caplog.records
               if r.name == _LOGGER and "DROPPED" in r.getMessage()]
    assert len(dropped) == 4, dropped


def _ambiguous(kind):
    if kind == "readtimeout":
        return httpx.ReadTimeout("The read operation timed out")
    return APIError({"message": "JSON could not be generated", "code": 524,
                     "hint": "Refer to full message for details", "details": "b''"})


@pytest.mark.parametrize("kind", ["readtimeout", "cf524"])
@pytest.mark.parametrize("k", [1, 2, 5])
@pytest.mark.asyncio
async def test_consecutive_unknown_outcomes_never_count_a_hit_more_than_twice(
        monkeypatch, caplog, kind, k):
    """W2L-1: k consecutive flushes commit and lose their response while 10 real taps arrive
    each minute. Merged back into `_pending`, the first minute's taps were re-sent — and
    committed — on every failing flush: 60 real taps became 210 in the table at k=5 while
    every WARNING said "may be counted twice". Now a hit gets ONE re-send and is dropped after
    a second unknown outcome, so every hit is counted at most twice, and exactly so here."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    ledger = _Ledger(script={"tiktok": [("after", _ambiguous(kind)) for _ in range(k)]})
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    key = ("tiktok", _DAY)
    for _ in range(k + 1):
        smart_link._add(key, 10)
        await smart_link.flush_hits()
    real = 10 * (k + 1)
    assert (smart_link._pending, smart_link._retry) == ({}, {}), "nothing left behind"
    # Minute 1 is sent once and re-sent once; every later minute rides one failing send (with
    # the previous minute's re-send) and one more send; the last minute lands first time.
    assert ledger.sent["tiktok"] == [10] + [20] * k
    assert ledger.db == {"tiktok": 20 * k + 10}
    assert ledger.db["tiktok"] <= 2 * real
    dropped = [r.getMessage() for r in caplog.records
               if r.name == _LOGGER and "DROPPED" in r.getMessage()]
    assert len(dropped) == k - 1, dropped
    assert all("10 hit(s) that were ALREADY a re-send" in m for m in dropped), dropped
    flagged = [r.getMessage() for r in caplog.records
               if r.name == _LOGGER and "will be re-sent ONCE" in r.getMessage()]
    assert len(flagged) == k and all("10 hit(s) had an UNKNOWN OUTCOME" in m for m in flagged)


@pytest.mark.asyncio
async def test_a_proven_non_commit_does_not_spend_the_one_re_send(monkeypatch, caplog):
    """A re-send that fails BEFORE the database (refused connection, migration 173 missing)
    counted nothing, so it keeps its single remaining attempt — held in `_retry`, never
    demoted to `_pending`, where a later unknown outcome would give it a second re-send."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    missing = APIError({"message": "Could not find the function", "code": "PGRST202",
                        "hint": None, "details": None})
    ledger = _Ledger(script={"tiktok": [
        ("after", httpx.ReadTimeout("t")),                # 40 committed, response lost
        ("before", httpx.ConnectError("refused")),       # the re-send never landed
        ("before", missing),                             # nor this one
        ("after", httpx.ReadTimeout("t")),                # the re-send commits, response lost
    ]})
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    key = ("tiktok", _DAY)
    smart_link._pending[key] = 40
    for _ in range(4):
        assert await smart_link.flush_hits() == 0
        if len(ledger.calls) < 4:
            assert (smart_link._pending, smart_link._retry) == ({}, {key: 40})
    assert (smart_link._pending, smart_link._retry) == ({}, {})
    assert await smart_link.flush_hits() == 0 and len(ledger.calls) == 4, "nothing to re-send"
    assert ledger.db == {"tiktok": 80}, "counted twice, never three times"


@pytest.mark.asyncio
async def test_the_rpc_ceiling_split_keeps_each_tier(monkeypatch):
    ledger = _Ledger(script={"tiktok": [("after", httpx.ReadTimeout("t"))]})
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    key = ("tiktok", _DAY)
    smart_link._retry[key] = smart_link._MAX_RPC_COUNT - 1
    smart_link._pending[key] = 5
    assert await smart_link.flush_hits() == 0
    # One RPC at the ceiling, re-send portion first: that portion's second unknown outcome
    # drops it, the one fresh hit sent moves to the re-send tier, the 4 unsent stay fresh.
    assert ledger.sent["tiktok"] == [smart_link._MAX_RPC_COUNT]
    assert (smart_link._pending, smart_link._retry) == ({key: 4}, {key: 1})
    assert await smart_link.flush_hits() == 5
    assert (smart_link._pending, smart_link._retry) == ({}, {})


@pytest.mark.asyncio
async def test_a_cancelled_flush_keeps_each_unattempted_key_in_its_own_tier(monkeypatch):
    ledger = _Ledger(block="tiktok")
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    smart_link._pending.update({("tiktok", _DAY): 2, ("youtube", _DAY): 4})
    smart_link._retry.update({("tiktok", _DAY): 1, ("youtube", _DAY): 3})
    task = asyncio.create_task(smart_link.flush_hits())
    try:
        await _until(ledger.entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # tiktok is in flight (at most once); youtube was never attempted and keeps its split.
        assert smart_link._pending == {("youtube", _DAY): 4}
        assert smart_link._retry == {("youtube", _DAY): 3}
    finally:
        ledger.release.set()
    await _until(lambda: ledger.finished == len(ledger.calls) == 1)
    assert ledger.db == {"tiktok": 3}


def test_the_docs_promise_at_least_once_and_at_most_twice():
    # The old docstring said counts were "never lost or double-sent"; a lost response after a
    # commit double-counts (pinned above), so nobody may read the number as exact — and the
    # bound they state ("twice") is the one the code enforces (pinned above too).
    flush_doc = " ".join((smart_link.flush_hits.__doc__ or "").split())
    module_doc = " ".join((smart_link.__doc__ or "").split())
    assert "AT-LEAST-ONCE" in flush_doc and "AT-LEAST-ONCE" in module_doc
    assert "AT MOST TWICE" in flush_doc and "AT MOST TWICE" in module_doc
    assert "double-sent" not in flush_doc
    assert "INDICATIVE" in module_doc


@pytest.mark.asyncio
async def test_a_flush_cancelled_mid_batch_keeps_unattempted_keys_and_not_the_in_flight_one(
        monkeypatch):
    """A deploy cancels the lifespan while a flush is inside key 2 of 3. Key 3 was never sent
    and must be kept; key 2's RPC is still running in its worker thread (cancellation cannot
    stop it) and DOES commit — re-queuing it would double-count it."""
    ledger = _Ledger(block="tiktok")
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    smart_link._pending.update({("instagram", _DAY): 1, ("tiktok", _DAY): 2,
                                ("youtube", _DAY): 3})
    task = asyncio.create_task(smart_link.flush_hits())
    try:
        await _until(ledger.entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert smart_link._pending == {("youtube", _DAY): 3}
    finally:
        ledger.release.set()
    await _until(lambda: ledger.finished == len(ledger.calls) == 2)
    assert ledger.db == {"instagram": 1, "tiktok": 2}, "the in-flight RPC still committed"
    assert smart_link._pending == {("youtube", _DAY): 3}


@pytest.mark.asyncio
async def test_a_loop_cancelled_mid_batch_counts_every_hit_exactly_once(monkeypatch):
    """End to end through the real loop: cancel mid-batch, let the bounded final flush run,
    then let the orphaned RPC land. Every recorded hit is in the database exactly once."""
    ledger = _Ledger(block="tiktok")
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    monkeypatch.setattr(smart_link, "_FLUSH_INTERVAL_SECONDS", 0.001)
    smart_link._pending.update({("instagram", _DAY): 1, ("tiktok", _DAY): 2,
                                ("youtube", _DAY): 3})
    task = asyncio.create_task(smart_link.run_link_hit_flush_loop())
    try:
        await _until(ledger.entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        ledger.release.set()
    await _until(lambda: ledger.finished == len(ledger.calls) and "tiktok" in ledger.db)
    assert ledger.db == {"instagram": 1, "tiktok": 2, "youtube": 3}
    assert sorted(ledger.calls) == ["instagram", "tiktok", "youtube"], ledger.calls
    assert smart_link._pending == {}


def _smart_link_warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == _LOGGER and r.levelno == logging.WARNING]


@pytest.mark.asyncio
async def test_a_flush_cancelled_mid_batch_still_reports_what_earlier_keys_dropped(
        monkeypatch, caplog):
    """W3L-2: the per-flush summary was logged only after the key loop finished, and the cancel
    branch re-raised before it. A re-send an EARLIER key dropped on its second unknown outcome
    (instagram's 40 below) vanished with no DROPPED line — the only log named the in-flight
    tiktok. The cancel branch now logs the batch's summary before it re-raises, and does not
    promise a re-send (the cancel may be the shutdown's own final flush)."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    ledger = _Ledger(script={"instagram": [("after", httpx.ReadTimeout("t"))]}, block="tiktok")
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    smart_link._retry.update({("instagram", _DAY): 40})
    smart_link._pending.update({("instagram", _DAY): 5, ("tiktok", _DAY): 7})
    task = asyncio.create_task(smart_link.flush_hits())
    try:
        await _until(ledger.entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        ledger.release.set()
    await _until(lambda: ledger.finished == len(ledger.calls) == 2)
    assert (smart_link._pending, smart_link._retry) == ({}, {("instagram", _DAY): 5})
    warned = _smart_link_warnings(caplog)
    summary = [m for m in warned if "flush CANCELLED" in m]
    assert len(summary) == 1, warned
    assert "1 key(s) / 40 hit(s) that were ALREADY a re-send" in summary[0], summary
    assert "DROPPED" in summary[0] and "campaign=instagram" in summary[0], summary
    assert "1 key(s) / 5 hit(s) had an UNKNOWN OUTCOME" in summary[0], summary
    assert "held for ONE re-send if another flush runs" in summary[0], summary
    assert "will be re-sent ONCE" not in summary[0], "a cancelled batch promises nothing"
    # Logged once — not again as an end-of-batch summary.
    assert not [m for m in warned if "failed and were kept for the next flush" in m], warned


@pytest.mark.asyncio
async def test_a_cancelled_flush_with_nothing_decided_adds_no_summary(monkeypatch, caplog):
    """The twin: a cancel that lands on the FIRST key has nothing earlier to report — only the
    in-flight line, no empty 'CANCELLED' summary."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    ledger = _Ledger(block="instagram")
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    smart_link._pending.update({("instagram", _DAY): 5, ("tiktok", _DAY): 7})
    task = asyncio.create_task(smart_link.flush_hits())
    try:
        await _until(ledger.entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        ledger.release.set()
    await _until(lambda: ledger.finished == len(ledger.calls) == 1)
    warned = _smart_link_warnings(caplog)
    assert len([m for m in warned if "flush cancelled mid-batch" in m]) == 1, warned
    assert not [m for m in warned if "flush CANCELLED" in m], warned


@pytest.mark.asyncio
async def test_what_the_final_flush_could_not_send_is_logged_as_lost_at_shutdown(
        monkeypatch, caplog):
    """W3L-2 (shutdown half): the final flush is the LAST one — what it keeps after a failure,
    and what its 5 s bound hands back unsent, dies with the process. It used to leave only
    "final flush failed (TimeoutError: )"; now the counts are named."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    ledger = _Ledger(raise_before_commit={"instagram": httpx.ConnectError("refused")},
                     block="tiktok")
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    monkeypatch.setattr(smart_link, "_FLUSH_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(smart_link, "_FINAL_FLUSH_TIMEOUT_SECONDS", 0.2)
    smart_link._pending.update({("instagram", _DAY): 5, ("tiktok", _DAY): 7,
                                ("youtube", _DAY): 3})
    smart_link._retry.update({("youtube", _DAY): 2})
    task = asyncio.create_task(smart_link.run_link_hit_flush_loop())
    try:
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        ledger.release.set()
    await _until(lambda: ledger.finished == len(ledger.calls) == 2)
    warned = _smart_link_warnings(caplog)
    lost = [m for m in warned if "LOST at shutdown" in m]
    assert len(lost) == 1, warned
    # instagram was refused before the database; youtube was never attempted (the bound cut
    # the batch at tiktok, whose RPC is in flight and not re-queued: at most once).
    assert "2 key(s) still held" in lost[0], lost
    assert f"instagram/{_DAY}=5+0" in lost[0] and f"youtube/{_DAY}=3+2" in lost[0], lost
    assert "8 never-sent hit(s) and 2 re-send hit(s)" in lost[0], lost
    assert "tiktok" not in lost[0], lost


@pytest.mark.asyncio
async def test_a_clean_final_flush_reports_no_loss(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    ledger = _Ledger()
    monkeypatch.setattr(smart_link, "get_supabase", lambda: ledger)
    monkeypatch.setattr(smart_link, "_FLUSH_INTERVAL_SECONDS", 3600)
    smart_link._pending.update({("instagram", _DAY): 5})
    task = asyncio.create_task(smart_link.run_link_hit_flush_loop())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger.db == {"instagram": 5}
    assert not [m for m in _smart_link_warnings(caplog) if "LOST" in m]


# ── 6. the flush loop ─────────────────────────────────────────────────────────


async def _until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_the_loop_survives_a_failing_cycle_and_final_flushes_on_cancel(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    calls = {"n": 0}

    async def fake_flush():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("one bad cycle")
        return 0

    monkeypatch.setattr(smart_link, "flush_hits", fake_flush)
    monkeypatch.setattr(smart_link, "_FLUSH_INTERVAL_SECONDS", 0.001)
    task = asyncio.create_task(smart_link.run_link_hit_flush_loop())
    await _until(lambda: calls["n"] >= 3)
    before = calls["n"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls["n"] == before + 1, "exactly one final flush on cancel"
    assert any(r.levelno == logging.ERROR and "flush failed" in r.getMessage()
               for r in caplog.records if r.name == _LOGGER)


@pytest.mark.asyncio
async def test_the_final_flush_is_bounded(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    async def hanging_flush():
        await asyncio.sleep(30)
        return 0

    monkeypatch.setattr(smart_link, "flush_hits", hanging_flush)
    monkeypatch.setattr(smart_link, "_FINAL_FLUSH_TIMEOUT_SECONDS", 0.05)
    task = asyncio.create_task(smart_link.run_link_hit_flush_loop())
    await asyncio.sleep(0.01)
    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 2.0
    assert any("final flush failed" in r.getMessage() for r in caplog.records
               if r.name == _LOGGER)


@pytest.mark.asyncio
async def test_the_loop_delivers_recorded_hits_through_the_real_flush(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(smart_link, "get_supabase", lambda: fake)
    monkeypatch.setattr(smart_link, "_FLUSH_INTERVAL_SECONDS", 0.001)
    smart_link._pending[("youtube", _DAY)] = 7
    task = asyncio.create_task(smart_link.run_link_hit_flush_loop())
    try:
        await _until(lambda: fake.calls)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert fake.calls == [_rpc("youtube", _DAY, 7)]
    assert smart_link._pending == {}


_MAIN_PY = _BACKEND / "app" / "main.py"
_FLUSH_SPAWN = '_spawn(smart_link.run_link_hit_flush_loop(), "marketing_link_hits_flush")'
#: What each production-only marketing loop must actually RUN (task name → coroutine call).
#: The bare-name form is accepted so an import-style refactor is not a false failure.
_PRODUCTION_LOOPS = {
    "marketing_link_hits_flush": {"smart_link.run_link_hit_flush_loop()",
                                  "run_link_hit_flush_loop()"},
    "marketing_publisher": {"run_marketing_publisher_loop()",
                            "publisher_service.run_marketing_publisher_loop()"},
}


def _production_spawns(src: str) -> dict:
    """`{task name: unparsed coroutine call}` for every `_spawn(...)` in the PRODUCTION branch
    of `lifespan` — the `else:` of `if is_local_dev:`. AST, not text: a comment that mentions
    the call must not satisfy it, and neither may a spawn on the local-dev path, which never
    runs on Railway."""
    import ast

    tree = ast.parse(src)
    lifespans = [n for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"]
    assert len(lifespans) == 1, "sentinel: main.py has no single `async def lifespan`"
    branches = [n for n in ast.walk(lifespans[0])
                if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                and n.test.id == "is_local_dev" and n.orelse]
    assert len(branches) == 1, "sentinel: expected exactly one `if is_local_dev: ... else:`"
    spawns = {}
    for stmt in branches[0].orelse:
        for node in ast.walk(stmt):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_spawn" and len(node.args) == 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                spawns[node.args[1].value] = ast.unparse(node.args[0])
    return spawns


def _assert_marketing_loops_run_in_production(src: str) -> None:
    spawns = _production_spawns(src)
    for name, allowed in _PRODUCTION_LOOPS.items():
        assert spawns.get(name) in allowed, (
            f"lifespan's production branch must spawn {name!r} as one of {sorted(allowed)}; "
            f"found {spawns.get(name)!r}. Without it /go keeps counting into memory and "
            f"nothing is ever written — silently."
        )


def test_the_lifespan_spawns_the_flush_loop_next_to_the_publisher():
    _assert_marketing_loops_run_in_production(_MAIN_PY.read_text(encoding="utf-8"))


def _remove_flush_spawn_line(src: str) -> str:
    out, n = re.subn(r"^[ \t]*" + re.escape(_FLUSH_SPAWN) + r"[ \t]*\n", "", src, flags=re.M)
    assert n == 1, "sentinel: the flush spawn line moved — update _FLUSH_SPAWN"
    return out


def _mutant_swapped_coroutine(src: str) -> str:
    assert src.count(_FLUSH_SPAWN) == 1, "sentinel: the flush spawn line moved"
    return src.replace(_FLUSH_SPAWN, '_spawn(asyncio.sleep(0), "marketing_link_hits_flush")')


def _mutant_moved_to_local_dev(src: str) -> str:
    src = _remove_flush_spawn_line(src)
    head = "    if is_local_dev:\n"
    assert src.count(head) == 1, "sentinel: the local-dev branch header moved"
    return src.replace(head, head + "        " + _FLUSH_SPAWN + "\n")


def _mutant_publisher_under_the_flush_name(src: str) -> str:
    assert src.count(_FLUSH_SPAWN) == 1, "sentinel: the flush spawn line moved"
    return src.replace(_FLUSH_SPAWN,
                       '_spawn(run_marketing_publisher_loop(), "marketing_link_hits_flush")')


@pytest.mark.parametrize("mutate", [
    _mutant_swapped_coroutine,              # the name survives, the loop does not run
    _mutant_moved_to_local_dev,             # runs on a laptop, never on Railway
    _remove_flush_spawn_line,               # deleted outright
    _mutant_publisher_under_the_flush_name,
], ids=["swapped", "local-dev", "deleted", "publisher-renamed"])
def test_the_lifespan_guard_fails_on_each_mutant(mutate):
    """testing.md rule 3, kept in the suite: the guard above must go red on every refactor
    that stops the flush loop from running in production. Mutated in memory — main.py is
    never written."""
    mutant = mutate(_MAIN_PY.read_text(encoding="utf-8"))
    compile(mutant, "main.py", "exec")  # the mutant is real, parseable code
    # Matched on the flush message: a sentinel tripping would also raise AssertionError and
    # make this pass for the wrong reason.
    with pytest.raises(AssertionError, match="must spawn 'marketing_link_hits_flush'"):
        _assert_marketing_loops_run_in_production(mutant)


# ── 7. the landing page ───────────────────────────────────────────────────────

_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'")
_ABS_URL_ATTR_RE = re.compile(
    r"""\b(?:src|href|action|formaction|poster|data|srcset|xlink:href)\s*=\s*["']?\s*"""
    r"""((?:https?:)?//[^"'\s>]*)""",
    re.IGNORECASE,
)
_CSS_URL_RE = re.compile(r"""url\(\s*["']?\s*(?:https?:)?//|@import""", re.IGNORECASE)


def _strip_markup(page: str) -> str:
    """The text a visitor reads: comments, <style> and tags removed, entities decoded."""
    page = re.sub(r"<!--.*?-->", " ", page, flags=re.S)
    page = re.sub(r"<(style|script)\b.*?</\1\s*>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<[^>]+>", " ", page)
    return compliance.clean(html.unescape(page))


def _compliance_violations(page: str):
    text = _strip_markup(page)
    # EXEMPTION 1 — the footer disclaimer is required legal copy. "investment advice" is on
    # BANNED_PHRASES because a MODEL writing a disclaimer is overstepping (post_copy owns it);
    # on this code-authored page the code owns it. Removed only as the exact sentence, once.
    assert text.count(_DISCLAIMER) == 1
    text = text.replace(_DISCLAIMER, " ")
    meta = re.search(r'<meta name="description" content="([^"]*)"', page)
    assert meta, "the page must carry a meta description"
    out = []
    for field, value in (("visible", text), ("description", compliance.clean(meta.group(1)))):
        for v in compliance.scan_text(field, value, allow_emoji=True):
            # EXEMPTION 2 — BRAND_TERMS stop the writer from authoring brand copy; this page IS
            # the brand's own page, so its name appears by design. Nothing else is exempt.
            if v.code == "brand_mention" and v.detail == "caydex":
                continue
            out.append(v.as_dict())
    return text, out


def test_the_landing_page_is_served_with_a_strict_csp(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers.get("content-security-policy") == _CSP
    assert r.headers.get("cache-control") == "public, max-age=300"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_head_on_the_landing_page(client):
    r = client.head("/")
    assert r.status_code == 200
    assert r.content == b""
    assert r.headers.get("content-security-policy") == _CSP


def test_the_csp_is_scoped_to_the_landing_page(client):
    # The legal pages are unaffected (their inline-style/links policy is a separate decision).
    assert "content-security-policy" not in {k.lower() for k in client.get("/privacy").headers}


def test_the_prelaunch_page_content(client):
    page = client.get("/").text
    assert "<title>Caydex</title>" in page
    for link in ('href="/privacy"', 'href="/terms"', 'href="/support"'):
        assert link in page
    assert _DISCLAIMER in page
    assert "Coming soon to the App Store" in page
    assert "apple-itunes-app" not in page
    assert "{{" not in page and "}}" not in page
    assert "<script" not in page.lower()
    assert not _ABS_URL_ATTR_RE.findall(page), _ABS_URL_ATTR_RE.findall(page)
    assert not _CSS_URL_RE.search(page)
    assert not re.search(r"<(?:iframe|object|embed|form|img|video|audio|link\s+rel=\"?stylesheet)",
                         page, re.I)
    assert "Caydex Inc" not in page and "Inc." not in page


#: Copy that promises notifications are OPT-IN. Such a sentence is only true if every kind in
#: the registry ships off — and 8 of 10 ship on (a single Allow on the onboarding prompt
#: turns them all on, and the in-app inbox needs no permission at all).
_OPT_IN_CLAIM_RE = re.compile(
    r"\b(?:off until|until you (?:turn|switch|enable)|opt(?:-| )?in(?:to)?\b|opted in\b|"
    r"only (?:if|when|once) you (?:turn|switch|enable|ask|choose))",
    re.IGNORECASE,
)
_NOTIFICATIONS_SENTENCE = ("Watchlists, portfolios you enter yourself, and alerts about what you "
                           "track, with a switch for each kind of alert.")


def _visible_prose(page: str) -> str:
    """The visible text with whitespace COLLAPSED: the template hard-wraps sentences, so a raw
    substring check misses a claim split across two source lines."""
    return " ".join(_strip_markup(page).split())


def _notification_claim_violations(page: str):
    text = _visible_prose(page)[:20_000]
    claims = [m.group(0) for m in _OPT_IN_CLAIM_RE.finditer(text)]
    on_by_default = sorted(k for k, kind in NOTIFICATION_KINDS.items() if kind.default_on)
    return [(claims, on_by_default)] if claims and on_by_default else []


@pytest.mark.parametrize("text,claims", [
    ("notifications that stay off until you turn them on", True),
    ("alerts are opt-in", True), ("you opt in to each alert", True),
    ("sent only if you turn them on", True),
    ("opting out of any alert is one switch", False), ("an optional digest", False),
    ("alerts about what you track, with a switch for each kind of alert", False),
])
def test_the_opt_in_claim_pattern(text, claims):
    assert bool(_OPT_IN_CLAIM_RE.search(text)) is claims


def test_the_landing_page_never_claims_notifications_are_opt_in(client):
    page = client.get("/").text
    assert _NOTIFICATIONS_SENTENCE in _visible_prose(page)
    assert not _notification_claim_violations(page), _notification_claim_violations(page)


def test_the_notification_claim_guard_is_not_vacuous(monkeypatch):
    """The sentence that shipped, hard-wrapped exactly as it was: a naive substring check
    passes on it, the normalised guard must not."""
    old = "notifications that stay off until you\n            turn them on."
    src, n = re.subn(r"(<h2>Your list, your pace</h2>\s*<p>).*?(</p>)",
                     lambda m: m.group(1) + "Watchlists, portfolios you enter yourself, and "
                     + old + m.group(2), _TEMPLATE.read_text(encoding="utf-8"), flags=re.S)
    assert n == 1, "sentinel: the card moved"
    assert "stay off until you turn them on" not in src  # why the guard normalises
    assert _notification_claim_violations(src)
    # And it is tied to the REGISTRY, not to a string: were every kind to ship off, the same
    # sentence would be true and allowed.
    all_off = {k: SimpleNamespace(default_on=False) for k in NOTIFICATION_KINDS}
    monkeypatch.setattr(sys.modules[__name__], "NOTIFICATION_KINDS", all_off)
    assert not _notification_claim_violations(src)


def test_the_post_launch_page_carries_the_store_button_and_smart_banner(client, launched):
    page = client.get("/").text
    assert f'<a class="cta" href="{_STORE}">' in page
    assert '<meta name="apple-itunes-app" content="app-id=6759525689">' in page
    assert page.count("apple-itunes-app") == 1
    assert "Coming soon" not in page
    # The ONLY absolute URL on the page is the configured store link.
    assert _ABS_URL_ATTR_RE.findall(page) == [_STORE]
    assert "<script" not in page.lower()


def test_the_store_url_is_escaped_into_the_href(client, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", f"{_STORE}?l=en&mt=8")
    page = client.get("/").text
    assert f'href="{_STORE}?l=en&amp;mt=8"' in page


def test_substitution_is_single_pass(client, monkeypatch):
    # A value containing a placeholder must come out literally, never expanded into markup.
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", f"{_STORE}/{{{{SMART_BANNER}}}}")
    page = client.get("/").text
    assert f'href="{_STORE}/{{{{SMART_BANNER}}}}"' in page
    assert page.count("apple-itunes-app") == 1


def test_a_store_url_without_an_id_gets_the_button_but_no_banner(client, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", "https://apps.apple.com/app/caydex")
    page = client.get("/").text
    assert 'href="https://apps.apple.com/app/caydex"' in page
    assert "apple-itunes-app" not in page


@pytest.mark.parametrize("post_launch", [False, True])
def test_the_visible_copy_passes_the_marketing_compliance_lists(client, monkeypatch, post_launch):
    if post_launch:
        monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", _STORE)
    text, violations = _compliance_violations(client.get("/").text)
    assert not violations, violations
    # No number of any kind in public copy: no price, no percentage, no return, no ticker count.
    assert not re.search(r"[0-9%$]", text), text


def test_the_compliance_harness_is_not_vacuous():
    """Anti-vacuity: the same harness must flag the things it exists to catch."""
    page = _TEMPLATE.read_text(encoding="utf-8").replace(
        "Research, not hype.",
        "Warren Buffett's top picks: the stock looks cheap, a 40% return.",
    )
    _, violations = _compliance_violations(page)
    codes = {v["code"] for v in violations}
    assert {"person_named", "banned_phrase", "class_b_valuation", "return_figure"} <= codes, codes


def test_the_template_has_each_placeholder_exactly_once():
    src = _TEMPLATE.read_text(encoding="utf-8")
    assert src.count("{{APP_STORE_CTA}}") == 1
    assert src.count("{{SMART_BANNER}}") == 1
    assert set(re.findall(r"\{\{([A-Z_]+)\}\}", src)) == {"APP_STORE_CTA", "SMART_BANNER"}
    # The banner slot must be inside <head> for Safari to read it.
    assert src.index("{{SMART_BANNER}}") < src.index("</head>")


def test_a_missing_template_is_a_logged_404(client, monkeypatch, tmp_path, caplog):
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "_SITE_DIR", tmp_path)
    caplog.set_level(logging.ERROR)
    r = client.get("/")
    assert r.status_code == 404
    assert any("Landing page could not be read" in rec.getMessage() for rec in caplog.records)


# ── 8. root-route allowlist ───────────────────────────────────────────────────

#: Every route outside `/api/v1`, with the reason it may answer an anonymous caller. The licence
#: gate (tests/test_account_only_licence_gate.py) scans `/api/v1` only; this is its other half.
_ROOT_ROUTES = {
    "/": "static, code-authored landing page; no data, no FMP, compliance-scanned above",
    "/go": "public smart link; 302 to `/` or the App Store, counts only",
    "/go/{campaign}": "public smart link; campaign normalised to a constant, 302 only",
    "/health": "readiness probe; status flags only, no data",
    "/health/live": "liveness probe; constant body",
    "/health/pdf": "deploy gate (railway.toml); renderer versions only, memoised",
    "/.well-known/apple-app-site-association": "Apple must fetch it unauthenticated; team+bundle id",
    "/privacy": "App Store-required Privacy Policy URL; static legal page",
    "/terms": "Terms of Use; static legal page",
    "/support": "App Store-required Support URL; static page",
}
#: FastAPI's own pages, mounted only when settings.DEBUG (app/main.py FastAPI(...)): they would
#: publish the whole API schema, so they must never exist in production.
_DEBUG_ONLY = {
    "/docs": "Swagger UI, DEBUG only",
    "/docs/oauth2-redirect": "Swagger UI helper, DEBUG only",
    "/redoc": "ReDoc, DEBUG only",
    "/openapi.json": "OpenAPI schema, DEBUG only",
}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _all_routes():
    import app.main as main_mod

    return [(getattr(r, "path", None), set(getattr(r, "methods", None) or ()))
            for r in main_mod.app.routes]


def test_every_root_route_is_allowlisted_with_a_reason():
    routes = _all_routes()
    api = [p for p, _ in routes if p and p.startswith("/api/v1")]
    root = [(p, m) for p, m in routes if p is not None and not p.startswith("/api/v1")]
    # Anti-vacuity: the scan reads the real app.
    assert len(api) >= 150 and len(root) >= len(_ROOT_ROUTES)
    unlisted = sorted(p for p, _ in root if p not in _ROOT_ROUTES and p not in _DEBUG_ONLY)
    assert not unlisted, (
        f"new route(s) outside /api/v1: {unlisted}. Every root route answers an anonymous "
        f"caller — add each to _ROOT_ROUTES with the reason that is acceptable (no FMP data, "
        f"no user data), or move it under /api/v1 where the licence gate covers it."
    )
    for path, methods in root:
        assert not (methods & _WRITE_METHODS), f"{path} accepts {methods & _WRITE_METHODS}"
    debug_present = sorted(p for p, _ in root if p in _DEBUG_ONLY)
    if debug_present:
        assert settings.DEBUG, f"{debug_present} mounted with DEBUG off — the API schema is public"


def test_the_allowlist_does_not_rot():
    paths = {p for p, _ in _all_routes()}
    missing = sorted(set(_ROOT_ROUTES) - paths)
    assert not missing, f"_ROOT_ROUTES lists routes that no longer exist: {missing}"


def test_the_smart_link_and_landing_routes_take_get_and_head_only():
    methods = {p: m for p, m in _all_routes() if p in ("/", "/go", "/go/{campaign}")}
    assert methods == {"/": {"GET", "HEAD"}, "/go": {"GET", "HEAD"},
                       "/go/{campaign}": {"GET", "HEAD"}}


# ── 9. import boundary ────────────────────────────────────────────────────────


def test_smart_link_never_loads_fmp_or_the_agents_package():
    """Public surfaces may never touch licensed market data (rules marketing.md §1). Checked in
    a fresh interpreter, because this process has long since imported everything."""
    code = (
        "import sys, app.services.marketing.smart_link\n"
        "print(sorted(m for m in sys.modules if m.startswith(('app.integrations', "
        "'app.services.agents'))))\n"
        "print('app.services.marketing.smart_link' in sys.modules)\n"
    )
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "SENTRY_DSN": ""}
    out = subprocess.run([sys.executable, "-c", code], cwd=_BACKEND, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    lines = out.stdout.strip().splitlines()
    assert lines[-1] == "True", "sentinel: the probe did not import the module"
    assert lines[-2] == "[]", lines[-2]
