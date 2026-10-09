"""Every module-level regex of the web-search gate runs in linear time (PLAN A8).

`chat_intent` scans a user message of up to 4,000 characters on EVERY turn (the ask kind, the
market-data classifier, the shadow topic), and `chat_web_search_service` scans every snippet and
every model-written query — on the single uvicorn worker. A catastrophic-backtracking pattern
there is a denial of service for every user. Same load-robust shape as
`test_chat_numeric_grounding`'s sweep: interleaved thread-CPU minima on a 4x rung (linear ≈ 4x,
quadratic ≈ 16x), confirmed by a second measurement.
"""

from __future__ import annotations

import gc
import re
import time
from typing import Callable, Tuple

import pytest

from app.services import chat_intent as ci
from app.services import chat_web_search_service as cws
from app.services import chat_chip_filter as ccf
from app.services import chat_security as cs


def _cpu_pair(small: Callable[[], object], big: Callable[[], object], rounds: int = 5) -> Tuple[float, float]:
    was = gc.isenabled()
    gc.disable()
    try:
        ts = tb = float("inf")
        for _ in range(rounds):
            t0 = time.thread_time()
            small()
            ts = min(ts, time.thread_time() - t0)
            t0 = time.thread_time()
            big()
            tb = min(tb, time.thread_time() - t0)
        return ts, tb
    finally:
        if was:
            gc.enable()


def _linear(rx, unit, small=3000, big=12000, floor=0.002, limit=8.0) -> bool:
    for _ in range(2):
        s = (unit * big)[:small]
        b = (unit * big)[:big]
        ts, tb = _cpu_pair(lambda: list(rx.finditer(s)), lambda: list(rx.finditer(b)))
        if tb < 0.25 and (tb < floor or tb / max(ts, 1e-6) < limit):
            return True
    return False


_UNITS = ("a.", "9.", "9,", "1 ", "$1.", "shares ", "rose ", "a ", "-", "%", " ", "s&p ", "www.",
          "<", "search ", "look ", "check ", "the ", "price ", "eur/", "usd ", "what is ", "1,0",
          "don't ", ". ", "?!", "vix ", "market ", "per ", "web ",
          # the 2026-10-09 static scrub, query and link shapes
          "a share ", "worth $", "+1% ", "AAPL 1.1 +1 ", "x:", "a.b/", "site:", "what's new with ",
          "where's ", "how much is ", "12345 ", "per dollar ",
          # the final review's shapes (2026-10-09)
          "ETH/USD 1 ", "1 ETH = ", "AAPL: $", "costs $", "AAPL close ", "usd jpy ", "settled at ",
          "ethereum ", "shiba inu ", "how did the ", "what did ", "usd5 ")

_NEW_PATTERNS = {
    ci: ("_WEB_EXPLICIT_RE", "_WEB_NEWS_RE", "_MARKET_DATA_RE"),
    cws: ("_CONSENT_DIGITS_RE", "_SENTENCE_SPLIT_RE", "_FIG_RE", "_MARKET_SUBJECT_RE",
          "_MOVE_VERB_RE", "_MOVE_CLAUSE_RE", "_FUNDAMENTAL_RE", "_MCAP_RE", "_PRICE_MARKER_RE",
          "_VOL_INDEX_RE", "_Q_MARKET_RE", "_Q_TICKER_PRICE_RE",
          # review 2026-10-09
          "_Q_MARKET_NOW_RE", "_STRONG_FIG_RE", "_STATIC_SUBJECT_RE", "_KEEP_SUBJECT_RE",
          "_FX_PAIR_RE", "_FX_PER_RE", "_SIGNED_PCT_RE", "_TICKER_QUOTE_RE", "_VALUE_CLAIM_RE",
          "_DEAL_RE", "_PRICE_UNIT_RE", "_Q_SCHEME_TOKEN_RE", "_Q_HOST_PATH_RE",
          "_Q_SITE_OPERATOR_RE", "_Q_URL_RE",
          # final review 2026-10-09
          "_Q_TICKER_CLOSE_RE", "_Q_FX_BARE_PAIR_RE", "_TICKER_COLON_QUOTE_RE", "_CRYPTO_PAIR_RE",
          "_COIN_RATE_RE", "_PRICE_VERB_RE", "_Q_PREFIXED_FIGURE_RE"),
    ccf: ("_SEARCH_THE_WEB_CHIP_RE",),
    cs: ("_WEB_CAVEAT_TAIL_RE",),
}


# Patterns used ONLY anchored (`.match` / `.fullmatch`) on a bounded string: `finditer` over 12,000
# chars is not how they run, so the sweep would measure the wrong thing. Each entry is pinned below
# to its anchored-only use (a `.search` / `.sub` / `.finditer` call on it fails that test).
_ANCHORED_ONLY = {
    "chat_intent._DEFINITIONAL_RE": "`.match` at offset 0 of the masked message",
    "chat_web_search_service._HOST_SHAPE_RE": "`.fullmatch` on a host already capped at 253 chars",
    "chat_web_search_service._CONSENT_DIGITS_RE": "`.fullmatch` on a header capped at 32 chars",
    "chat_web_search_service._ISO_DATE_RE": "`.match` on a page_age string",
    "chat_web_search_service._Q_YEAR_RE": "`.fullmatch` on one query token",
    "chat_web_search_service._Q_PERIOD_RE": "`.fullmatch` on one query token",
    "chat_web_search_service._Q_PRODUCT_RE": "`.fullmatch` on one query token",
}


def _patterns():
    out = []
    for mod in (ci, cws, ccf, cs):
        out += [(f"{mod.__name__.rsplit('.', 1)[-1]}.{n}", v) for n, v in vars(mod).items()
                if isinstance(v, re.Pattern)]
    out += [(f"chat_intent._TOPIC_TABLE.{label}", rx) for label, rx in ci._TOPIC_TABLE]
    return out


def _swept():
    return [(n, rx) for n, rx in _patterns() if n not in _ANCHORED_ONLY]


@pytest.mark.parametrize("name", sorted(_ANCHORED_ONLY))
def test_an_exempt_pattern_is_only_ever_used_anchored(name):
    import inspect
    mod_name, attr = name.split(".", 1)
    mod = {"chat_intent": ci, "chat_web_search_service": cws}[mod_name]
    src = inspect.getsource(mod)
    uses = re.findall(rf"\b{re.escape(attr)}\.(\w+)\(", src)
    assert uses, f"{name} is never used — drop it from the exemptions"
    assert set(uses) <= {"match", "fullmatch"}, (name, uses)


def test_the_sweep_covers_every_new_pattern():
    names = {n for n, _ in _patterns()}
    for mod, wanted in _NEW_PATTERNS.items():
        short = mod.__name__.rsplit(".", 1)[-1]
        for n in wanted:
            assert f"{short}.{n}" in names, n
    assert len([n for n in names if "_TOPIC_TABLE" in n]) == len(ci._TOPIC_TABLE)


@pytest.mark.parametrize("name,rx", _swept(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_pattern_is_linear(name, rx):
    slow = [unit for unit in _UNITS if not _linear(rx, unit)]
    assert slow == [], (name, slow)


def test_the_linearity_check_is_not_vacuous():
    assert not _linear(re.compile(r"(?:[a-z]\.)+Z"), "a.")


@pytest.mark.parametrize("fn", [ci.web_ask_kind, ci.is_market_data_question, ci.web_fallback_topic,
                                cws._scrub_market_figures, cws._is_market_data_query,
                                cws.sanitize_web_query])
@pytest.mark.parametrize("unit", ["search the web ", "a.", "rose 1% ", "price ", ". " , "x" * 7 + " "])
def test_every_public_scan_is_fast_on_a_hostile_4000_char_message(fn, unit):
    payload = (unit * 4000)[:4000]
    started = time.perf_counter()
    fn(payload)
    assert time.perf_counter() - started < 0.25
