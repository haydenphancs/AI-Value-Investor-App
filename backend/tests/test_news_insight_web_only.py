"""A card written from web pages ALONE (2026-11-12, owner: "Web search fills it").

JOBY moved −6.9% midweek with no FMP story in its 48 h window, so the sweeper had nothing to
write from. A live jumper with zero FMP news now gets a card from the jump search's pages —
under stricter rules than a mixed card, because the pages are the only support:

- no model call when no page says anything beyond the price, when no such page is from a
  publisher we name, or when no page can be stored as a tappable source;
- every shown point, the headline and the conclusion are checked against the pages: a point
  no page supports is dropped, an unsupported headline takes the one retry, then the card is
  refused; no point left → refused;
- at most 2 points (the web-only MOVE rule asks for 1 or 2; one point + the conclusion is
  still the 2 bullets the DB CHECK needs);
- page text never reaches a log or `last_error` — counts only;
- a card with articles is unchanged (the golden prompt test pins the non-web prompt).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pytest

from app.services import news_insight_service as nis
from app.services.news_insight_service import (
    MIN_BULLETS,
    WEB_ONLY_MAX_POINTS,
    WEB_ONLY_REASON,
    NewsInsightService,
    _page_has_news,
    _title_cased,
    _web_only_pages,
    _web_rows,
    is_web_only_row,
    price_terms,
)

NOW = datetime(2026, 11, 12, 18, 45, tzinfo=timezone.utc)
PUBLISHED = datetime(2026, 11, 12, 16, 52, tzinfo=timezone.utc)

DUBAI = {
    "title": "Joby delays its Dubai air taxi launch to 2027",
    "snippet": "Joby Aviation said certification work with the GCAA pushed its commercial "
               "launch in Dubai to 2027 and cut its 2026 delivery target to 4 aircraft.",
    "url": "https://www.reuters.com/business/aerospace/joby-dubai-delay-2026-11-12/",
    "publisher": "Reuters", "published_at": PUBLISHED,
}
OFFERING = {
    "title": "Joby Aviation prices $500 million share offering",
    "snippet": "The air taxi maker priced a $500 million offering of common stock to fund "
               "certification and manufacturing.",
    "url": "https://www.cnbc.com/2026/11/12/joby-aviation-offering.html",
    "publisher": "CNBC", "published_at": PUBLISHED,
}
PRICE_TALK = {
    "title": "Why Joby Aviation Stock Is Sinking Today",
    "snippet": "Joby Aviation (JOBY) shares are trading lower Wednesday. Here's what's going on.",
    "url": "https://www.cnbc.com/2026/11/12/why-joby-is-sinking.html",
    "publisher": "CNBC", "published_at": PUBLISHED,
}
UNNAMED = {**DUBAI, "url": "https://stockchatter.example/joby-dubai", "publisher": "stockchatter"}

PAGE_WORDS = ("Dubai", "GCAA", "certification", "offering", "Sinking", "air taxi")


def _card(headline="Joby delays its Dubai launch to 2027",
          points=("Joby said certification work pushed its Dubai launch to 2027.",
                  "Joby priced a $500 million share offering to fund manufacturing."),
          conclusion="The Dubai delay and the offering both turn on certification.",
          sentiment="bearish"):
    return {"headline": headline, "points": list(points), "sentiment": sentiment,
            "conclusion": conclusion}


# A card the Dubai page alone supports (the default conclusion's "offering" is on OFFERING only).
DUBAI_ONLY = _card(points=("Joby said certification work pushed its Dubai launch to 2027.",),
                   conclusion="The Dubai launch now turns on certification work.")


class _Gemini:
    """Answers each call from a script (the last answer repeats); records every call."""

    def __init__(self, *cards, repairs=()):
        self.cards = list(cards) or [_card()]
        self.repairs = list(repairs)
        self.calls = []

    async def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("usage_tag") == nis._USAGE_TAG_REPAIR:
            answer = self.repairs.pop(0) if self.repairs else None
            return {"text": json.dumps({"conclusion": answer} if answer else {})}
        card = self.cards.pop(0) if len(self.cards) > 1 else self.cards[0]
        return {"text": json.dumps(card)}

    @property
    def card_calls(self):
        return [c for c in self.calls if c.get("usage_tag") == nis._USAGE_TAG_CARD]


class _Svc(NewsInsightService):
    def __init__(self, gemini=None):
        self.supabase = None
        self._cache = {}
        self._inflight = {}
        self.gemini = gemini
        self.store_args = None

    def _store(self, *args, **kwargs):
        self.store_args = (args, kwargs)
        return True


async def _run(svc, web, *, corpus=(), scope="JOBY", reason="web_only (2)"):
    return await svc.generate_and_store(
        scope=scope, corpus=list(corpus), inputset_id="iid", price_band="Unusual",
        trigger_reason=reason, quote=None, market_active=True, now=NOW,
        company_name="Joby Aviation, Inc.", web_context=web, jumping=True,
    )


def _no_page_text(caplog, reason=""):
    for text in [reason, *(r.getMessage() for r in caplog.records)]:
        for word in PAGE_WORDS:
            assert word not in text, (word, text)


# ── the gate in generate_and_store ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_web_only_card_is_written_with_only_web_sources():
    gem = _Gemini()
    svc = _Svc(gem)
    card = await _run(svc, [DUBAI, OFFERING])
    assert card is not None
    assert card["bullets"][-1] == _card()["conclusion"] and len(card["bullets"]) == 3
    args, _ = svc.store_args
    scope, _c, _iid, trigger, count, _active, sources = args
    assert trigger == "web_only (2)" and count == 2
    assert [s.get("origin") for s in sources] == ["web", "web"]
    assert [s["url"] for s in sources] == [DUBAI["url"], OFFERING["url"]]
    stored = nis._sanitize_sources(sources, keep_origin=True)
    assert is_web_only_row({"trigger_reason": trigger, "sources": stored})
    prompt = gem.card_calls[0]["prompt"]
    assert "these 2 web pages mean for JOBY" in prompt
    assert "There are no news articles about JOBY; the web pages below are the only source" in prompt
    assert "- MOVE (web pages only)." in prompt and "Write 1 or 2 points" in prompt
    assert "summarise the news as usual" not in prompt
    assert "<<<ARTICLE" not in prompt and "Articles (UNTRUSTED" not in prompt
    assert "<<<WEB 1>>>" in prompt


def test_the_web_only_prompt_does_not_depend_on_the_jumping_flag():
    svc = _Svc()
    web = _web_rows([DUBAI])
    a = svc._build_prompt("JOBY", [], "x", "Unusual", None, now=NOW, web_context=web, jumping=True)
    b = svc._build_prompt("JOBY", [], "x", "Unusual", None, now=NOW, web_context=web)
    assert a == b and "- MOVE (web pages only)." in a
    # No figure and no direction of the move enters it.
    assert "6.9" not in a and "down" not in a.split("- MOVE (web pages only).")[1].split("\n")[0]


@pytest.mark.asyncio
async def test_no_articles_and_no_pages_is_still_an_empty_corpus():
    gem = _Gemini()
    svc = _Svc(gem)
    assert await _run(svc, None) is None
    assert svc.pop_failure_reason("JOBY") == "empty corpus" and not gem.calls
    # Junk pages are no pages.
    assert await _run(svc, ["junk", {"title": "", "url": "https://x.example"}, 5]) is None
    assert svc.pop_failure_reason("JOBY") == "empty corpus" and not gem.calls


@pytest.mark.asyncio
async def test_the_market_card_never_goes_web_only():
    gem = _Gemini()
    svc = _Svc(gem)
    assert await _run(svc, [DUBAI], scope="__MARKET__") is None
    assert svc.pop_failure_reason("__MARKET__") == "empty corpus" and not gem.calls


@pytest.mark.asyncio
async def test_price_talk_pages_make_no_model_call(caplog):
    caplog.set_level(logging.WARNING)
    gem = _Gemini()
    svc = _Svc(gem)
    assert await _run(svc, [PRICE_TALK, {**PRICE_TALK, "url": "https://www.reuters.com/j"}]) is None
    reason = svc.pop_failure_reason("JOBY")
    assert reason == "web_only_no_content: price talk only (pages=2)"
    assert not gem.calls and svc.store_args is None
    _no_page_text(caplog, reason)


@pytest.mark.asyncio
async def test_pages_only_from_unnamed_publishers_make_no_model_call(caplog):
    caplog.set_level(logging.WARNING)
    gem = _Gemini()
    svc = _Svc(gem)
    # A named publisher's page that is price talk does not vouch for an unnamed one's news.
    assert await _run(svc, [UNNAMED, PRICE_TALK]) is None
    reason = svc.pop_failure_reason("JOBY")
    assert reason == "web_only_no_content: no known publisher (pages=2, with news=1)"
    assert not gem.calls
    _no_page_text(caplog, reason)


@pytest.mark.asyncio
async def test_a_named_page_with_news_lets_an_unnamed_one_ride_along():
    gem = _Gemini(DUBAI_ONLY)
    svc = _Svc(gem)
    assert await _run(svc, [UNNAMED, DUBAI]) is not None
    assert len(svc.store_args[0][6]) == 2


@pytest.mark.asyncio
async def test_pages_with_no_storable_link_make_no_model_call():
    gem = _Gemini()
    svc = _Svc(gem)
    redirect = {**DUBAI, "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x"}
    too_long = {**OFFERING, "url": "https://www.cnbc.com/" + "a" * 600}
    assert await _run(svc, [redirect, too_long]) is None
    assert svc.pop_failure_reason("JOBY") == "web_only_no_source: 0 of 2 page(s) linkable"
    assert not gem.calls


@pytest.mark.asyncio
async def test_an_unlinkable_page_is_neither_read_nor_listed():
    gem = _Gemini(DUBAI_ONLY)
    svc = _Svc(gem)
    redirect = {**OFFERING, "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/y"}
    assert await _run(svc, [DUBAI, redirect]) is not None
    assert "priced a $500 million" not in gem.card_calls[0]["prompt"]
    assert [s["url"] for s in svc.store_args[0][6]] == [DUBAI["url"]]


@pytest.mark.asyncio
async def test_the_store_is_refused_when_no_web_source_survives_sanitizing(monkeypatch):
    """The belt behind `_web_only_pages`: a card whose sources sanitize to nothing has no
    link to show and would be served as an ordinary card."""
    gem = _Gemini(DUBAI_ONLY)
    svc = _Svc(gem)
    monkeypatch.setattr(nis, "_web_only_pages", lambda scope, web, name=None: (web, None))
    redirect = {**DUBAI, "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/z"}
    assert await _run(svc, [redirect]) is None
    assert svc.pop_failure_reason("JOBY") == "web_only_no_source: 0 of 1 page(s) linkable"
    assert svc.store_args is None


@pytest.mark.asyncio
async def test_a_reason_without_the_web_only_tag_is_tagged():
    svc = _Svc(_Gemini())
    assert await _run(svc, [DUBAI, OFFERING], reason="band Notable->Unusual (-8.25%) + web (2)")
    assert svc.store_args[0][3] == "web_only + band Notable->Unusual (-8.25%) + web (2)"
    svc = _Svc(_Gemini())
    assert await _run(svc, [DUBAI, OFFERING], reason="")
    assert svc.store_args[0][3] == WEB_ONLY_REASON
    # Past the stored clip the tag would be lost: it is put first.
    svc = _Svc(_Gemini())
    assert await _run(svc, [DUBAI, OFFERING], reason="x" * 300 + " web_only")
    assert svc.store_args[0][3].startswith("web_only + x")


# ── grounding: points, headline, conclusion ─────────────────────────────────

@pytest.mark.asyncio
async def test_a_point_naming_what_no_page_states_is_dropped(caplog):
    caplog.set_level(logging.WARNING)
    gem = _Gemini(_card(points=(
        "Joby said certification work pushed its Dubai launch to 2027.",
        "Joby has a partnership with Toyota and Delta on air taxis.",
    ), conclusion="The Dubai launch now turns on certification work."))
    svc = _Svc(gem)
    card = await _run(svc, [DUBAI])
    assert card is not None and len(gem.card_calls) == 1
    assert card["bullets"] == ["Joby said certification work pushed its Dubai launch to 2027.",
                               "The Dubai launch now turns on certification work."]
    assert len(card["bullets"]) >= MIN_BULLETS
    assert any("written without 1 point(s) the pages do not support" in r.getMessage()
               for r in caplog.records)
    _no_page_text(caplog)


@pytest.mark.asyncio
async def test_a_point_with_a_figure_no_page_gives_is_dropped():
    gem = _Gemini(_card(points=(
        "Joby said certification work pushed its Dubai launch to 2027.",
        "Joby plans to deliver $2 billion of aircraft by 2028.",
    ), conclusion="The Dubai launch now turns on certification work."))
    svc = _Svc(gem)
    card = await _run(svc, [DUBAI])
    assert card["bullets"][0].startswith("Joby said certification") and len(card["bullets"]) == 2


@pytest.mark.asyncio
async def test_a_figure_on_a_page_is_supported():
    gem = _Gemini(_card(points=(
        "Joby cut its 2026 delivery target to 4 aircraft as Dubai slipped to 2027.",
        "Joby priced a $500 million share offering to fund manufacturing.",
    ), conclusion="Certification sets the pace for both deliveries and the launch."))
    card = await _run(_Svc(gem), [DUBAI, OFFERING])
    assert card is not None and len(card["bullets"]) == 3


@pytest.mark.asyncio
async def test_no_supported_point_left_takes_the_retry_then_is_refused(caplog):
    caplog.set_level(logging.WARNING)
    bad = _card(points=("Joby has a partnership with Toyota on air taxis.",
                        "Joby plans to deliver $2 billion of aircraft by 2028."),
                conclusion="Joby's plans turn on its partners.")
    gem = _Gemini(bad, bad)
    svc = _Svc(gem)
    assert await _run(svc, [DUBAI]) is None
    assert len(gem.card_calls) == 2
    retry = gem.card_calls[1]["prompt"]
    assert "REPAIR." in retry and "the web pages do not support" in retry
    reason = svc.pop_failure_reason("JOBY")
    assert reason.startswith("web_only_guard: ") and reason.endswith("[web pages: 1]"), reason
    assert "Toyota" not in reason and svc.store_args is None
    assert all("Toyota" not in r.getMessage() for r in caplog.records)
    _no_page_text(caplog, reason)


@pytest.mark.asyncio
async def test_an_unsupported_headline_figure_is_retried_and_the_fix_is_kept():
    bad = _card(headline="Joby delays its Dubai launch and cuts 30% of staff")
    good = _card()
    gem = _Gemini(bad, good)
    card = await _run(_Svc(gem), [DUBAI, OFFERING])
    assert len(gem.card_calls) == 2 and card["headline"] == good["headline"]


@pytest.mark.asyncio
async def test_an_unsupported_headline_name_is_refused_after_the_retry():
    bad = _card(headline="Joby delays its Dubai launch as Archer wins approval")
    gem = _Gemini(bad, bad)
    svc = _Svc(gem)
    assert await _run(svc, [DUBAI, OFFERING]) is None
    assert len(gem.card_calls) == 2
    reason = svc.pop_failure_reason("JOBY")
    assert reason.startswith("web_only_guard: 2 item(s), text withheld"), reason


@pytest.mark.asyncio
async def test_a_title_cased_headline_takes_the_retry_and_the_sentence_case_one_is_kept():
    # Every word of it is on a page: the Title Case ALONE takes the retry, and the
    # sentence-case answer wins the tie.
    title_case = _card(headline="Joby Delays Its Dubai Launch To 2027")
    gem = _Gemini(title_case, _card())
    card = await _run(_Svc(gem), [DUBAI, OFFERING])
    assert len(gem.card_calls) == 2 and card["headline"] == _card()["headline"]
    assert nis._SENTENCE_CASE_NOTE in gem.card_calls[1]["prompt"]


@pytest.mark.asyncio
async def test_a_title_cased_headline_the_pages_support_word_for_word_is_written():
    """Still Title-Cased after the retry: every capital is checked against the pages and the
    kept points — and here each one is there."""
    title_case = _card(headline="Joby Delays Its Dubai Launch To 2027")
    gem = _Gemini(title_case, title_case)
    card = await _run(_Svc(gem), [DUBAI, OFFERING])
    assert card is not None and len(gem.card_calls) == 2
    assert card["headline"] == title_case["headline"]


@pytest.mark.parametrize("headline", [
    # Review 2026-11-12: the Title-Case exemption turned the name check OFF — the more names
    # the model invented, the surer the exemption.
    "Joby Delays Dubai Launch As Toyota, Delta Pull Back",
    "Joby delays Dubai as Toyota Motor, Delta Air Lines exit",
    "Joby Pushes Back Its Dubai Launch To 2027",        # "Back": no page, no point says it
    "Joby Delays Dubai Launch Amid Lawsuit",             # an event no page states
])
@pytest.mark.asyncio
async def test_a_title_cased_headline_with_a_word_no_page_states_is_refused(headline, caplog):
    caplog.set_level(logging.WARNING)
    bad = _card(headline=headline)
    gem = _Gemini(bad, bad)
    svc = _Svc(gem)
    assert await _run(svc, [DUBAI, OFFERING]) is None
    assert len(gem.card_calls) == 2
    assert nis._SENTENCE_CASE_NOTE in gem.card_calls[1]["prompt"]
    reason = svc.pop_failure_reason("JOBY")
    assert reason.startswith("web_only_guard:") and "Toyota" not in reason, reason
    _no_page_text(caplog, reason)


@pytest.mark.asyncio
async def test_more_than_two_supported_points_are_cut_to_two():
    gem = _Gemini(_card(points=(
        "Joby said certification work pushed its Dubai launch to 2027.",
        "Joby cut its 2026 delivery target to 4 aircraft.",
        "Joby priced a $500 million share offering to fund manufacturing.",
    )))
    card = await _run(_Svc(gem), [DUBAI, OFFERING])
    assert len(card["bullets"]) == WEB_ONLY_MAX_POINTS + 1


@pytest.mark.asyncio
async def test_a_conclusion_figure_on_no_page_is_refused():
    gem = _Gemini(_card(conclusion="The delay could cost Joby $900 million in revenue."))
    svc = _Svc(gem)
    assert await _run(svc, [DUBAI, OFFERING]) is None
    reason = svc.pop_failure_reason("JOBY")
    assert reason.startswith("conclusion_guard: figure 1 item(s), text withheld"), reason


@pytest.mark.asyncio
async def test_a_conclusion_figure_from_a_page_is_supported():
    """`_fabricated` reads the pages' titles and snippets when there is no article: the
    "$500 million" is in no point and not in the headline — only on the OFFERING page."""
    gem = _Gemini(_card(points=("Joby said certification work pushed its Dubai launch to 2027.",),
                        conclusion="Certification work, backed by $500 million, sets the "
                                   "pace in Dubai."))
    card = await _run(_Svc(gem), [DUBAI, OFFERING])
    assert card is not None and "$500 million" in card["bullets"][-1]
    assert len(gem.card_calls) == 1


@pytest.mark.asyncio
async def test_a_conclusion_name_no_page_states_is_repaired_or_refused():
    draft = _card(conclusion="The Dubai delay hands rival Archer a head start.")
    gem = _Gemini(draft, repairs=["The Dubai delay and the offering both turn on certification."])
    card = await _run(_Svc(gem), [DUBAI, OFFERING])
    assert card is not None and "Archer" not in card["bullets"][-1]
    gem = _Gemini(draft, repairs=[])
    svc = _Svc(gem)
    assert await _run(svc, [DUBAI, OFFERING]) is None
    assert svc.pop_failure_reason("JOBY").startswith("web_only_guard:")


@pytest.mark.asyncio
async def test_the_repair_budget_warning_withholds_draft_text(monkeypatch, caplog):
    """It logged `first_check.reasons()` and the price issues verbatim — a web-fed draft
    copies page titles word for word (Brave terms: nothing kept outside the card)."""
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(nis, "_REPAIR_BUDGET_SECONDS", -1.0)
    gem = _Gemini(_card(headline="Joby Aviation stock falls 7% on GCAA certification work"))
    svc = _Svc(gem)
    assert await _run(svc, [DUBAI, OFFERING]) is None
    budget = [r.getMessage() for r in caplog.records if "skipping the repair" in r.getMessage()]
    assert budget and "text withheld (web-fed card)" in budget[0]
    _no_page_text(caplog, svc.pop_failure_reason("JOBY"))


# ── review 2026-11-12: bare numbers, acronyms, unlisted events, over-refusals ──

GOOD_POINT = "Joby said certification work pushed its Dubai launch to 2027."
GOOD_CONCLUSION = "The Dubai launch now turns on certification work."


@pytest.mark.parametrize("point", [
    "Joby said certification work pushed its Dubai launch to 2029.",      # wrong year
    "Joby will cut 400 jobs and close 3 plants.",                          # bare counts
    "Joby said 12 aircraft are now in certification work for Dubai.",      # a count, not a date
])
@pytest.mark.asyncio
async def test_a_bare_number_no_page_gives_drops_the_point(point):
    gem = _Gemini(_card(points=(GOOD_POINT, point), conclusion=GOOD_CONCLUSION))
    card = await _run(_Svc(gem), [DUBAI])
    assert card is not None and card["bullets"] == [GOOD_POINT, GOOD_CONCLUSION]


@pytest.mark.asyncio
async def test_a_bare_year_in_the_headline_is_retried_then_refused():
    bad = _card(headline="Joby delays its Dubai launch to 2029", points=(GOOD_POINT,),
                conclusion=GOOD_CONCLUSION)
    gem = _Gemini(bad, bad)
    svc = _Svc(gem)
    assert await _run(svc, [DUBAI]) is None
    assert len(gem.card_calls) == 2 and 'number "2029"' in gem.card_calls[1]["prompt"]
    assert svc.pop_failure_reason("JOBY").startswith("web_only_guard:")


@pytest.mark.asyncio
async def test_a_bare_number_in_the_conclusion_is_repaired_or_refused():
    draft = _card(points=(GOOD_POINT,), conclusion="The Dubai launch now waits until 2029.")
    gem = _Gemini(draft, repairs=[GOOD_CONCLUSION])
    card = await _run(_Svc(gem), [DUBAI])
    assert card is not None and card["bullets"][-1] == GOOD_CONCLUSION
    repair = [c for c in gem.calls if c.get("usage_tag") == nis._USAGE_TAG_REPAIR]
    assert len(repair) == 1 and 'conclusion number "2029"' in repair[0]["prompt"]
    gem = _Gemini(draft, repairs=[])
    svc = _Svc(gem)
    assert await _run(svc, [DUBAI]) is None
    assert svc.pop_failure_reason("JOBY").startswith("web_only_guard:")


@pytest.mark.parametrize("point", [
    # The prompt's own TIME rule asks for "on Thursday, Sep 10": the month abbreviation is no
    # new name and the day of the month is no new number.
    "Joby said on Thursday, Nov 12 that certification work pushed its Dubai launch to 2027.",
    "Joby said on Nov. 12 that certification work pushed its Dubai launch to 2027.",
    # The company's own name, over a page that says only "Joby".
    "Joby Aviation said certification work pushed its Dubai launch to 2027.",
    # A second sentence opening on an ordinary word.
    "Joby said certification work pushed its Dubai launch to 2027. Shipments follow later.",
])
@pytest.mark.asyncio
async def test_what_the_guard_used_to_refuse_is_written(point):
    """Review 2026-11-12: each of these was REFUSED after 2 calls — one of a web-only scope's
    2 daily failures spent on an honest card."""
    joby_only = {**DUBAI, "snippet": "Joby said certification work with the GCAA pushed its "
                                     "commercial launch in Dubai to 2027."}
    gem = _Gemini(_card(points=(point,), conclusion=GOOD_CONCLUSION))
    card = await _run(_Svc(gem), [joby_only])
    assert card is not None and card["bullets"] == [point, GOOD_CONCLUSION]
    assert len(gem.card_calls) == 1


@pytest.mark.parametrize("point", [
    "The FAA grounded the fleet after a test flight crashed.",              # an acronym
    "A prototype crashed during testing and the chief executive resigned.",  # unlisted events
    "Toyota said the Dubai launch moved after certification work slipped.",  # name, sentence start
])
@pytest.mark.asyncio
async def test_an_invented_fact_no_list_names_is_dropped(point):
    gem = _Gemini(_card(points=(GOOD_POINT, point), conclusion=GOOD_CONCLUSION))
    card = await _run(_Svc(gem), [DUBAI, OFFERING])
    assert card is not None and card["bullets"][:-1] == [GOOD_POINT]


@pytest.mark.parametrize("point", [
    "The FAA grounded the fleet after a test flight crashed.",
    "A prototype crashed during testing and the chief executive resigned.",
])
@pytest.mark.asyncio
async def test_an_invented_fact_alone_is_refused(point, caplog):
    caplog.set_level(logging.WARNING)
    bad = _card(points=(point,), conclusion=GOOD_CONCLUSION)
    gem = _Gemini(bad, bad)
    svc = _Svc(gem)
    assert await _run(svc, [DUBAI, OFFERING]) is None
    assert len(gem.card_calls) == 2
    reason = svc.pop_failure_reason("JOBY")
    assert reason.startswith("web_only_guard: ") and "FAA" not in reason, reason
    _no_page_text(caplog, reason)


@pytest.mark.asyncio
async def test_a_dropped_price_point_keeps_the_refusal_a_price_guard_one():
    """The sweeper alerts watchers on `price_guard`; a card that lost one point to price talk
    and the other to grounding is still that refusal (review 2026-11-12)."""
    bad = _card(points=("Joby stock fell 7% in the session.",
                        "Joby has a partnership with Toyota on air taxis."),
                conclusion=GOOD_CONCLUSION)
    gem = _Gemini(bad, bad)
    svc = _Svc(gem)
    assert await _run(svc, [DUBAI]) is None
    assert svc.pop_failure_reason("JOBY").startswith("price_guard: ")
    # Grounding alone empties it → web_only_guard.
    bad = _card(points=("Joby has a partnership with Toyota on air taxis.",),
                conclusion=GOOD_CONCLUSION)
    svc = _Svc(_Gemini(bad, bad))
    assert await _run(svc, [DUBAI]) is None
    assert svc.pop_failure_reason("JOBY").startswith("web_only_guard: ")


@pytest.mark.asyncio
async def test_a_title_only_page_that_names_a_cause_is_written_from():
    page = {"title": "Joby Aviation stock falls after Dubai delay", "snippet": None,
            "url": "https://www.reuters.com/business/joby-dubai-delay/", "publisher": "Reuters",
            "published_at": PUBLISHED}
    point = "Joby Aviation faces a Dubai delay."
    gem = _Gemini(_card(headline="Joby faces a Dubai delay", points=(point,),
                        conclusion="The Dubai delay is the story behind the session."))
    card = await _run(_Svc(gem), [page])
    assert card is not None and card["bullets"][0] == point


# ── mixed cards are unchanged ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_mixed_card_is_not_ground_checked_against_the_pages():
    corpus = [{"headline": "Joby and Toyota expand their manufacturing partnership",
               "summary": "Toyota adds investment.", "article_url": "https://fmp.example/1",
               "published_at": "2026-11-12T13:00:00Z"}]
    gem = _Gemini(_card(points=("Joby said certification work pushed its Dubai launch to 2027.",
                                "Joby and Toyota expanded their manufacturing partnership.",
                                "Joby priced a $500 million share offering."),
                        conclusion="Certification and Toyota's backing shape Joby's next year."))
    svc = _Svc(gem)
    card = await _run(svc, [DUBAI, OFFERING], corpus=corpus, reason="band (+9%) + web (2)")
    assert card is not None and len(card["bullets"]) == 4        # no cap, no drop
    prompt = gem.card_calls[0]["prompt"]
    assert "MOVE (web pages only)" not in prompt and "Articles (UNTRUSTED" in prompt
    assert svc.store_args[0][3] == "band (+9%) + web (2)"          # never tagged
    assert [s.get("origin") for s in svc.store_args[0][6]] == ["web", "web", None]


# ── pure helpers: outliers ──────────────────────────────────────────────────

TERMS = ["JOBY", *price_terms("JOBY", "Joby Aviation, Inc.")]


@pytest.mark.parametrize("page", [
    None, 5, "Joby delays Dubai", {}, {"title": None, "snippet": 7},
    {"title": "   ", "snippet": ""}, {"title": ["Joby", "Dubai"]},
    {"title": "Why Joby Aviation Stock Is Sinking Today"},
    {"title": "JOBY stock down 6.9% as investors weigh valuation"},
    {"title": "Joby Aviation (JOBY) shares are trading lower Wednesday."},
])
def test_page_has_news_is_false_for_junk_and_price_talk(page):
    assert _page_has_news(page, TERMS) is False


def test_page_has_news_reads_the_news_left_beside_price_talk():
    assert _page_has_news(DUBAI, TERMS)
    assert _page_has_news({"title": "Joby Aviation Stock Drops After Pricing $500 Million "
                                    "Share Offering"}, TERMS)
    # Garbage terms are ignored, not fatal.
    assert _page_has_news(DUBAI, [None, 5, ""])


@pytest.mark.parametrize("text,expected", [
    ("Joby Aviation Delays Dubai Launch After FAA Review", True),
    ("Joby Aviation delays Dubai launch after FAA review", False),
    ("Joby", False), ("", False), (None, False), (42, False),
    ("JOBY GCAA FAA NASA", False),
])
def test_title_cased(text, expected):
    assert _title_cased(text) is expected


def test_web_only_pages_outliers():
    assert _web_only_pages("JOBY", []) == ([], "web_only_no_source: 0 of 0 page(s) linkable")
    junk = [None, "x", {"title": "t"}, {"title": "t", "url": 5}]
    pages, reason = _web_only_pages("JOBY", junk)
    assert pages == [] and reason == "web_only_no_source: 0 of 4 page(s) linkable"
    pages, reason = _web_only_pages("JOBY", [*junk, DUBAI], "Joby Aviation, Inc.")
    assert pages == [DUBAI] and reason is None
    # No company name: the symbol alone still recognises the price talk.
    assert _web_only_pages("JOBY", [PRICE_TALK])[1] == (
        "web_only_no_content: price talk only (pages=1)"
    )


@pytest.mark.parametrize("title", [
    "Joby Aviation stock falls after Dubai delay",
    "JOBY shares drop on FAA delay",
    "Joby Aviation shares slide following GCAA certification setback",
])
def test_page_has_news_reads_a_cause_after_the_price_talk(title):
    assert _page_has_news({"title": title}, TERMS) is True


@pytest.mark.parametrize("title", [
    "Joby stock falls on heavy volume",
    "Joby Aviation shares slide amid broader market selloff",
    "Joby Aviation stock sinks as investors weigh valuation",
    "Joby stock drops after Dubai",                      # one word is not a cause
])
def test_page_has_news_still_refuses_a_price_only_cause(title):
    assert _page_has_news({"title": title}, TERMS) is False


PAGES = [f"{DUBAI['title']} {DUBAI['snippet']}"]
SUBJECT = nis._ground_subject_words(TERMS)


@pytest.mark.parametrize("text", [None, 5, "", "   ", ["Toyota"]])
def test_web_only_flags_is_clean_on_junk(text):
    assert nis._web_only_flags(text, PAGES, subject_words=SUBJECT) == []


def test_web_only_flags_outliers():
    flags = nis._web_only_flags
    # No page at all: every claim is unsupported, nothing raises.
    assert flags("Joby met Toyota in 2029.", [], subject_words=SUBJECT) == [
        'number "2029"', 'new name "Toyota"']
    assert flags("Joby met Toyota.", [None, 7], subject_words=SUBJECT) == ['new name "Toyota"']
    # Day of the month after a month or weekday name; not after anything else.
    assert flags("Joby said on Sept 30 and Fri 2 that Dubai slipped.", PAGES,
                 subject_words=SUBJECT) == []
    assert flags("Joby said in 30 days that Dubai slipped.", PAGES,
                 subject_words=SUBJECT) == ['number "30"']
    assert flags("Joby said on Nov 45 that Dubai slipped.", PAGES,
                 subject_words=SUBJECT) == ['number "45"']
    # The symbol's own digits are its name.
    assert flags("1INCH said Dubai slipped to 2027.", PAGES, subject_words=frozenset(),
                 symbols=["1INCH"]) == []
    assert flags("1INCH said Dubai slipped to 2027.", PAGES, subject_words=frozenset()) == [
        'number "1"']
    # A number written with a separator or trailing zero is the same number.
    assert flags("Joby cut deliveries to 4.0 aircraft.", PAGES, subject_words=SUBJECT) == []
    # Acronyms: case-sensitive against the pages; the clock is no name.
    assert flags("The GCAA set the 4 p.m. ET review.", PAGES, subject_words=SUBJECT) == []
    assert flags("The gcaa work and the FAA.", PAGES, subject_words=SUBJECT) == [
        'new name "FAA"']
    # A capital mid-sentence is judged even when it is a known non-name ("Shipments" opens a
    # sentence only at its start).
    assert flags("Joby said Shipments follow.", PAGES, subject_words=SUBJECT) == [
        'new name "Shipments"']
    # Hyphenated names are judged part by part.
    assert flags("Joby-Toyota work in Dubai.", PAGES, subject_words=SUBJECT) == [
        'new name "Toyota"']
    # Title mode: every capital, on a page or in the support text.
    assert flags("Joby Delays Dubai Launch", PAGES, subject_words=SUBJECT, title=True) == []
    assert flags("Joby Wins Dubai Nod", PAGES, subject_words=SUBJECT, title=True) == [
        'new name "Wins"', 'new name "Nod"']
    assert flags("Joby Wins Dubai Nod", PAGES, subject_words=SUBJECT, title=True,
                 support=["Joby wins a nod in Dubai."]) == []


def test_web_only_flags_coverage_counts_content_words_stem_insensitively():
    flags = nis._web_only_flags
    # "delayed" ~ "delays", "deliveries" ~ "delivery": all on the page.
    assert flags("Joby delayed Dubai and cut deliveries.", PAGES, subject_words=SUBJECT,
                 coverage=True) == []
    assert flags("Joby resigned amid crashes during hearings.", PAGES, subject_words=SUBJECT,
                 coverage=True) == ["wording: 0 of 3 content words on a page"]
    # 3 of 5 on a page is exactly the 60% bar: kept. 2 of 4 is not.
    assert flags("Joby certification work pushed Dubai, alarming regulators.", PAGES,
                 subject_words=SUBJECT, coverage=True) == []
    assert flags("Joby certification work alarmed regulators.", PAGES,
                 subject_words=SUBJECT, coverage=True) == [
        "wording: 2 of 4 content words on a page"]
    # Nothing but stop words and the subject: nothing to judge.
    assert flags("Joby said that.", PAGES, subject_words=SUBJECT, coverage=True) == []


# ── review round 3 (2026-11-12): whole words, trading-only causes, generic acronyms ──

INTEL_PAGE = {
    **DUBAI,
    "snippet": DUBAI["snippet"] + " The company also uses artificial intelligence in "
               "affordable metal parts.",
}


@pytest.mark.parametrize("name", ["Intel", "Ford", "Meta"])
def test_a_name_hidden_inside_a_page_word_is_still_new(name):
    pages = [f"{INTEL_PAGE['title']} {INTEL_PAGE['snippet']}"]
    assert nis._web_only_flags(f"Joby delays Dubai as {name} steps in.", pages,
                               subject_words=SUBJECT) == [f'new name "{name}"']
    # Title mode reads its support the same whole-word way.
    assert f'new name "{name}"' in nis._web_only_flags(
        f"Joby Delays Dubai As {name} Steps In", pages, subject_words=SUBJECT, title=True,
        support=["The company uses artificial intelligence in affordable metal parts."])


@pytest.mark.asyncio
async def test_an_invented_name_inside_a_page_word_is_refused_end_to_end(caplog):
    caplog.set_level(logging.WARNING)
    bad = _card(headline="Joby delays Dubai launch as Intel steps back")
    gem = _Gemini(bad, bad)
    svc = _Svc(gem)
    assert await _run(svc, [INTEL_PAGE, OFFERING]) is None
    reason = svc.pop_failure_reason("JOBY")
    assert reason.startswith("web_only_guard:"), reason
    _no_page_text(caplog, reason)


def test_has_word_outliers():
    assert nis._has_word("intel is here", "intel") is True
    assert nis._has_word("artificial intelligence", "intel") is False
    assert nis._has_word("intel's chips", "intel") is True
    assert nis._has_word("", "intel") is False and nis._has_word("intel", "") is False


@pytest.mark.parametrize("title", [
    "Joby Aviation stock jumps on short squeeze",
    "Joby shares rally as shorts cover",
    "Joby stock surges on heavy options activity",
    "Joby stock falls on rate fears",
    "Joby Aviation shares climb amid retail buying frenzy",
])
def test_a_trading_mechanics_cause_is_not_news(title):
    assert _page_has_news({"title": title}, TERMS) is False


@pytest.mark.asyncio
async def test_squeeze_pages_make_no_model_call():
    squeeze = {**PRICE_TALK, "title": "Joby Aviation stock jumps on short squeeze",
               "snippet": ""}
    gem = _Gemini()
    svc = _Svc(gem)
    assert await _run(svc, [squeeze]) is None
    assert not gem.calls
    assert svc.pop_failure_reason("JOBY").startswith("web_only_no_content:")


def test_generic_role_and_region_acronyms_are_not_new_names():
    pages = ["Joby's chief executive said the U.S. launch is on track in Dubai."]
    assert nis._web_only_flags("The CEO said Joby's US launch is on track.", pages,
                               subject_words=SUBJECT) == []
    # An agency is still a name the pages must print.
    assert nis._web_only_flags("The FAA said Joby's US launch is on track.", pages,
                               subject_words=SUBJECT) == ['new name "FAA"']


@pytest.mark.asyncio
async def test_title_mode_lets_a_capital_stand_on_a_kept_point_end_to_end():
    """Title mode wired through generate_and_store: "Schedule" is on no page, only in a kept
    point; "Its"/"To" are known non-names mid-headline. Without title mode the headline is
    refused."""
    point = "Joby said certification work pushed its Dubai schedule to 2027."
    title_case = _card(headline="Joby Pushes Its Dubai Schedule To 2027", points=(point,),
                       conclusion="The Dubai launch now turns on certification work.")
    gem = _Gemini(title_case, title_case)
    card = await _run(_Svc(gem), [DUBAI])
    assert card is not None and card["headline"] == title_case["headline"]
    assert len(gem.card_calls) == 2
