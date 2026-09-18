"""`chat_starters_service.is_tape_bound` must agree with the chips the service GENERATES.

The pre-warmed answer table (migration 162) stores no `kind`, so the warm service's read
path classifies a stored question by TEXT to decide whether its answer can go stale (a
"What tickers are hot today?" row from 09:35 must not be served at 15:50). A template
edited in one place and not the other would silently reclassify a live chip as evergreen
— and the stale replay this exists to stop would be back with no test failing. So every
generator is driven here with inputs that make it fire, and its output is fed through the
classifier; every evergreen chip in the bundled catalogue must classify False.

No network.
"""

from __future__ import annotations

import pytest

from app.services import chat_starters_service as starters


def _profile(symbol, change):
    return {
        "symbol": symbol, "companyName": symbol, "price": 100.0, "marketCap": 5e10,
        "volume": 1e7, "averageVolume": 1e7, "changePercentage": change,
        "exchange": "NASDAQ", "isEtf": False, "isFund": False,
        "sector": "Technology", "industry": "Semiconductors",
    }


@pytest.fixture
def svc():
    return starters.ChatStartersService()


def _generated_tape_chips(svc):
    universe = {"NVDA": _profile("NVDA", 14.2), "INTC": _profile("INTC", -7.4),
                "TSLA": _profile("TSLA", 5.5)}
    change_map = {s: p["changePercentage"] for s, p in universe.items()}
    sources = {
        "scanner_inputs": (universe, change_map),
        "sectors": [{"sector": "Basic Materials", "changesPercentage": -2.6},
                    {"sector": "Technology", "changesPercentage": 1.1}],
        "themes": [{"category": "AI Infrastructure", "tickers": ["NVDA", "INTC"]}],
        "mentions": {"TSLA": {"rank": 1}},
    }
    chips = list(svc._hot_ticker_slots(sources))
    for one in (svc._hot_sector_slot(sources), svc._hot_topic_slot(sources),
                svc._trending_slot(sources)):
        if one is not None:
            chips.append(one)
    chips += [starters.ChatStarterResponse(text=t, kind="fixed")
              for t in starters._FIXED_TAPE_TEXTS]
    return chips


def test_every_generated_tape_chip_classifies_as_tape_bound(svc):
    chips = _generated_tape_chips(svc)
    kinds = {c.kind for c in chips}
    assert kinds == starters.TAPE_KINDS, (
        f"fixture did not make every tape kind fire: {kinds ^ starters.TAPE_KINDS}"
    )
    for chip in chips:
        assert starters.is_tape_bound(chip.text), (chip.kind, chip.text)


def test_the_bundled_evergreen_pool_never_classifies_as_tape_bound():
    pool = starters._BUNDLED.get(starters._GLOBAL_SCOPE) or []
    assert len(pool) >= 8, "fixture: the bundled global pool is the evergreen floor"
    for text in pool:
        assert not starters.is_tape_bound(text), text


def test_the_assembled_row_agrees_with_the_classifier_chip_by_chip(svc):
    """The end-to-end shape: `_assemble` tags every chip with a `kind`; the classifier
    must reproduce `kind in TAPE_KINDS` from the text alone."""
    live = _generated_tape_chips(svc)[:4]
    row = svc._assemble("2026-09-14", {}, live)
    assert len(row.global_starters) == starters._GLOBAL_SLOTS
    for chip in row.global_starters:
        assert starters.is_tape_bound(chip.text) == (chip.kind in starters.TAPE_KINDS), (
            chip.kind, chip.text
        )


@pytest.mark.parametrize("text", [
    "", None, "   ", "Why is NVDA up today?", "Why is NVDA up 14 today?",
    "what tickers are hot", "Why is everyone talking about?", "What's driving today?",
])
def test_near_misses_are_not_tape_bound(text):
    assert not starters.is_tape_bound(text)


def test_cosmetic_differences_do_not_reclassify():
    assert starters.is_tape_bound("  WHAT   tickers are hot today?")
    assert starters.is_tape_bound("why is nvda UP 14% today?")


# ── the chips carry the SESSION word, not a hard-coded "today" (2026-09-17) ──

def test_a_prior_session_row_words_the_chip_on_its_weekday(monkeypatch, svc):
    """From Friday's close until Monday's open the universe still carries Friday's
    stamp. "Why is BBNX up 15% today?" on a Saturday was a false claim; the chip now
    says "on Fri" — and still classifies as tape-bound."""
    from datetime import date
    import app.services.chat_starters_service as mod
    saturday = date(2026, 9, 12)
    monkeypatch.setattr(mod, "_session_word", lambda stamp: (
        "today" if not stamp or date.fromisoformat(str(stamp)[:10]) >= saturday
        else f"on {date.fromisoformat(str(stamp)[:10]).strftime('%a')}"))
    universe = {"NVDA": {**_profile("NVDA", 14.2), "changeSession": "2026-09-11"}}
    sources = {
        "scanner_inputs": (universe, {"NVDA": 14.2}),
        "sectors": [{"sector": "Technology", "changesPercentage": 2.6, "date": "2026-09-11"}],
        "themes": [{"category": "AI Infrastructure", "tickers": ["NVDA"]}],
        "mentions": {},
    }
    texts = [c.text for c in svc._hot_ticker_slots(sources)]
    texts.append(svc._hot_sector_slot(sources).text)
    texts.append(svc._hot_topic_slot(sources).text)
    assert texts == ["Why is NVDA up 14% on Fri?", "Why is Technology leading on Fri?",
                     "What's driving AI Infrastructure on Fri?"]
    for t in texts:
        assert starters.is_tape_bound(t), t


def test_session_word_reads_the_stamp_against_the_et_calendar_day():
    from datetime import date, datetime, timedelta
    import app.services.chat_starters_service as mod
    import app.utils.market_hours as mh
    today = datetime.now(mh.ET).date()
    assert mod._session_word(None) == "today"
    assert mod._session_word("") == "today"
    assert mod._session_word("garbage") == "today"
    assert mod._session_word(today.isoformat()) == "today"
    assert mod._session_word((today + timedelta(days=1)).isoformat()) == "today", "never a future weekday"
    yesterday = today - timedelta(days=1)
    assert mod._session_word(yesterday.isoformat()) == f"on {yesterday.strftime('%a')}"
    assert mod._session_word(yesterday.isoformat() + "T21:00:00Z") == f"on {yesterday.strftime('%a')}"
