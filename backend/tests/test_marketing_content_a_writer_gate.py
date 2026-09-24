"""
The writer's gate end to end (`writer_service.validate_package`) for the content-A review:
people pointed at without a lexicon name, promises that contradict the disclaimer, links,
quotations, testimonials, CTAs, markup and accents.

Why a separate file: `compliance.scan_text` and `grounding.check_grounding` are unit-tested in
their own files, but what reaches a public post is decided by `validate_package` — the shared
fields (hook, script, cards, slides) must be clean or the whole package is rejected, and a failing
caption drops only its outlet. Every attack string below was ACCEPTED by `validate_package` before
the fix (the review's repro), so each test here is the end-to-end proof, per item, that it is not
any more: in a SHARED field the package is rejected, and as the X caption the X post is dropped.

The baseline package for each item is built from that item's OWN fact sentences, and is asserted
clean first — otherwise a rejection would prove nothing about the injected string.

Category 1 (pure): no network, no Supabase; the Learn bundle and the vendored word lists only.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any, Dict, List, Tuple

import pytest

from app.services.marketing import content_pool
from app.services.marketing import writer_service as ws

RUN_DATE = date(2026, 9, 21)


def _usable(item: content_pool.ContentItem) -> List[str]:
    """Whole fact sentences of a comfortable length that pass the item's own scan."""
    out = [s for s in item.fact_sentences
           if s.endswith(".") and 6 <= len(s.split()) <= 20 and "Mr." not in s
           and not ws._scan("x", s, item, allow_emoji=False)]
    assert len(out) >= 12, (item.key, len(out))
    return out


def _baseline(key: str) -> Tuple[content_pool.ContentItem, Dict[str, Any]]:
    item = content_pool.get_item(key)
    assert item is not None and item.eligible, key
    g = _usable(item)
    short = min(g, key=len)
    pkg = {
        "hook": min(g[:6], key=lambda s: len(s.split())),
        "video_script": g[:8],
        "cards": [{"title": " ".join(g[i].split()[:4]).rstrip(",.:;"), "body": g[i + 1]}
                  for i in (0, 2, 4)],
        "carousel_slides": [{"title": " ".join(g[i].split()[:4]).rstrip(",.:;"),
                             "body": f"{g[i + 1]} {g[i + 2]}"} for i in range(0, 10, 2)],
        "captions": {
            "tiktok": " ".join(g[0:2]), "youtube_title": short,
            "youtube_description": " ".join(g[2:4]), "instagram": " ".join(g[3:5]),
            "facebook": " ".join(g[4:6]), "x": short, "threads": g[6], "bluesky": short,
            "linkedin": " ".join(g[:3]),
        },
    }
    base = ws.validate_package(pkg, item, RUN_DATE)
    assert base.ok and not base.violations, (key, [(v.field, v.code, v.detail)
                                                   for v in base.violations])
    return item, pkg


def _with(pkg: Dict[str, Any], field: str, text: str) -> Dict[str, Any]:
    out = copy.deepcopy(pkg)
    if field == "hook":
        out["hook"] = text
    elif field == "script":
        out["video_script"][0] = text
    else:
        out["captions"][field] = text
    return out


def _assert_rejected_everywhere(key: str, text: str, code: str) -> None:
    item, pkg = _baseline(key)
    shared = ws.validate_package(_with(pkg, "hook", text), item, RUN_DATE)
    assert not shared.ok, (key, text)
    assert code in {v.code for v in shared.shared if v.field == "hook"}, (
        key, text, [(v.code, v.detail) for v in shared.shared])
    cap = ws.validate_package(_with(pkg, "x", text), item, RUN_DATE)
    assert "x" not in cap.posts, (key, text)
    assert code in {v.code for v in cap.outlets.get("x", [])}, (
        key, text, [(v.code, v.detail) for v in cap.outlets.get("x", [])])
    # The other outlets still carry the item: the rejection is scoped, never a lost day.
    assert cap.ok and len(cap.posts) == len(ws.PLATFORMS) - 1


# ── people (idx 0, 2, 16, 29, 31) ────────────────────────────────────────────


@pytest.mark.parametrize("key, text", [
    ("money_moves:microsofts-cloud-metamorphosis", "Microsoft's CEO famously called Linux a cancer."),
    ("money_moves:microsofts-cloud-metamorphosis", "A new CEO turned Microsoft toward the cloud."),
    ("money_moves:tesla-vs-traditional-auto", "Tesla's own CEO called it production hell."),
    ("money_moves:the-rise-of-lvmh", "One man has run LVMH for three decades."),
    ("money_moves:the-rise-of-lvmh", "LVMH's chairman set the pattern for the industry."),
    ("money_moves:how-amazon-built-its-moat", "Amazon's founder was famously patient about profit."),
    ("money_moves:nvidias-ai-dominance", "Nvidia's leather-jacketed CEO saw this coming."),
    ("money_moves:metas-metaverse-pivot", "Its founder still controls the vote."),
    ("journey:mr_market", "The father of value investing created Mr. Market."),
    ("journey:mr_market", "A legendary value investor made this idea famous."),
    ("journey:mr_market", "Mr. Market was invented by the man who taught value investing."),
    ("journey:mr_market", "Uncle Warren's favourite habit is patience with Mr. Market."),
    ("journey:mr_market", "Ben invented Mr. Market to teach patience."),
    ("journey:mr_market", "A Buffet-style habit is patience with Mr. Market."),
    ("money_moves:netflix-vs-disney-plus", "Walt Disney built a studio that still shapes streaming."),
    ("money_moves:tesla-vs-traditional-auto", "Henry Ford's assembly line still shapes the incumbents."),
    ("money_moves:the-rise-of-lvmh", "Christian Dior's house kept its own designers."),
    ("money_moves:metas-metaverse-pivot", "Mark renamed Facebook to Meta Platforms."),
    ("money_moves:how-amazon-built-its-moat", "Jeff built Amazon into a flywheel."),
    ("money_moves:apples-services-revolution", "Cook kept the focus on services."),
    ("money_moves:apples-services-revolution", "Apple changed course under Cook."),
    # The executives the raw corpus names, by surname, on their own company's item.
    ("money_moves:how-amazon-built-its-moat", "Bezos didn't blink."),
    ("money_moves:microsofts-cloud-metamorphosis", "Nadella changed course at Microsoft."),
    ("money_moves:the-rise-of-lvmh", "Arnault built LVMH."),
    ("money_moves:tsmc-the-foundry-that-runs-the-world", "Morris Chang founded TSMC."),
])
def test_a_real_person_never_reaches_a_post(key, text):
    _assert_rejected_everywhere(key, text, "person_named")


def test_a_verbatim_copy_of_a_formerly_kept_role_sentence_is_rejected():
    """The review's most natural path: the writer copies the fact sheet. The sentence is gone
    from the sheet now, and the validator refuses it if a model supplies it from memory."""
    item, pkg = _baseline("money_moves:tesla-vs-traditional-auto")
    text = ("Tesla nearly went bankrupt more than once during what its own CEO called "
            "'production hell.'")
    res = ws.validate_package(_with(pkg, "threads", text), item, RUN_DATE)
    assert "threads" not in res.posts
    assert "person_named" in {v.code for v in res.outlets["threads"]}


@pytest.mark.parametrize("key, text", [
    ("money_moves:the-rise-of-lvmh", "Louis Vuitton began making trunks in 1854."),
    ("money_moves:netflix-vs-disney-plus", "Disney built its streaming arm on franchises."),
    ("money_moves:costcos-membership-magic", "Costco sells in bulk."),
    ("journey:red_flags", "When the executives who know the company best are quietly cashing "
                          "out, ask what they might see."),
])
def test_brands_and_generic_roles_still_pass(key, text):
    item, pkg = _baseline(key)
    res = ws.validate_package(_with(pkg, "x", text), item, RUN_DATE)
    assert "x" in res.posts, (key, text, [(v.code, v.detail) for v in res.outlets.get("x", [])])


# ── promises and the disclaimer's subject (idx 1) ────────────────────────────


@pytest.mark.parametrize("key, text, code", [
    ("journey:power_of_discipline", "Over the long run, the market always goes up.", "promissory"),
    ("journey:etfs_101", "Index funds are a safe way to grow your money.", "promissory"),
    ("journey:fomo_cycle", "The market always recovers, so never sell in a panic.", "promissory"),
    ("journey:compound_interest", "Compounding guarantees your money grows.", "promissory"),
    ("journey:mr_market", "Patient investors never lose money.", "promissory"),
    ("money_moves:costcos-membership-magic", "Patient shareholders never lose money.", "promissory"),
    ("journey:mr_market", "Treat this lesson as personal advice.", "code_owned"),
    ("journey:mr_market", "No AI was used to write this lesson.", "code_owned"),
    ("journey:mr_market", "This is not a disclaimer.", "code_owned"),
])
def test_text_that_contradicts_the_code_owned_disclaimer_is_rejected(key, text, code):
    _assert_rejected_everywhere(key, text, code)


# ── links, quotations, testimonials, CTAs, markup, accents (idx 13-15, 17, 18, 46) ─


@pytest.mark.parametrize("text, code", [
    ("Free investor education lives at investor.gov.", "link"),
    ("Read more at caydexinvest。com today.", "link"),
    ("Be greedy when others are fearful, and fearful when others are greedy.", "famous_quote"),
    ("Price is what you pay. Value is what you get.", "famous_quote"),
    ("Want to know how I stopped panic selling?", "first_person"),
    ("Our readers say this lesson changed everything.", "first_person"),
    ("Endorsed by the SEC.", "endorsement"),
    ("Try it free for a week.", "cta"),
    ("Get the full lesson in the app.", "cta"),
    ("Mr. Market is *moody*.", "markup"),
    ("Warren Buffétt loved Mr. Market.", "person_named"),
    ("Written with Gémini.", "identity_leak"),
])
def test_links_quotes_testimonials_ctas_markup_and_accents_are_rejected(text, code):
    _assert_rejected_everywhere("journey:mr_market", text, code)


def test_an_entity_is_decoded_before_it_is_stored():
    item, pkg = _baseline("journey:mr_market")
    res = ws.validate_package(_with(pkg, "x", "Mr. Market &amp; you."), item, RUN_DATE)
    assert "x" in res.posts
    assert res.package["captions"]["x"] == "Mr. Market & you."
    assert "&amp;" not in res.posts["x"].caption
