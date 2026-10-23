"""The in-app weekly validators, run over the 23 hand-made Money Moves articles.

Plan "Wiser weekly content engine", Phase 0 "Owner fix list" + §1.7 "Corpus test": run the planned
in-app validator over every bundle block and turn every hit into an OWNER FIX LIST — not into
exceptions in the validators. This file pins that list EXACTLY, so:

* a bundle edit that fixes an item fails here until the item is removed from the list (the list
  shrinks as the owner works through it), and
* a validator change that starts or stops flagging a bundle block fails here, with the diff — the
  bundle is the real-draft corpus that tells over-blocking from a real catch.

Run: `validate_block(field, block, section_kind="history", grounding_ctx=None)` on every block of
every article in `backend/data/money_moves.json` (the vendored copy the seeder reads; byte-equal
to the iOS bundle today). "history" because a bundle article has no peg section — so every dated
time word counts — and no grounding, because a bundle article has no fact sheet.

Reading the list (2026-10-22). Two kinds of hit, and only the first is something to fix:

1. **Owner-actionable wording** (advice, verdicts, quotes) — the Phase-0 fix list:
   - `class_b_recommendation`: costcos-membership-magic "buy low"; how-amazon-built-its-moat
     "worth owning"; metas-metaverse-pivot "should own"; the-fall-of-sears "buy when", "hold the
     shares" (the Sears verdict); the-rise-of-lvmh "buy it"; warren-buffetts-early-days
     "buy when".
   - `banned_phrase` + `code_owned`: the-future-of-digital-finance "financial advice" / "advice"
     (already moving off the hero slot — plan Phase 0).
   - `famous_quote`: the-fall-of-sears "time is the friend of the…"; warren-buffetts-early-days
     "market is a voting…", "far better to buy a wonderful…"; `misquote`: the Buffett article's
     voting-machine line — correctly credited for the bundle
     (`test_learn_content_misattributions.py`), but a weekly piece carries no quotes at all.
   - `time_word`: "today" (Amazon, Enron, LVMH), "this month" (Netflix), "next week" (Costco) —
     evergreen copy that dates itself; an owner judgement call per line.
   - `advice_directive` (added 2026-10-23 with the trade-verb and reader's-holdings rows, review
     F2): warren-buffetts-early-days, the Mr. Market parable's "offering to buy your shares" —
     second person about the reader's own holdings, which a weekly piece never writes. The
     chat-style advice rows still fire nowhere in the bundle (pinned below).
   - Likely false positives to note, not fix: the-fall-of-enron `code_owned` "fine print" (the
     lesson is to READ the fine print); the-rise-of-tiktok-vs-instagram-reels `link` "musical.ly"
     (a company name the autolinker would linkify — the marketing scan refuses it too).
2. **Weekly-only shape rules** — correct for a generated piece, not a defect in a hand-made one:
   `word_limit` (bundle blocks were voiced before the 75-word Chatterbox cap), `block_type`
   (bundle quote blocks), `person_named` (bundle case studies name founders and CEOs; weekly
   pieces name people by role only) and `conduct_claim` (the Enron / FTX / Theranos misconduct
   stories, already EXCLUDED from marketing in `content_pool.py`).

⚠️ Editing a narrated `text` to clear an item means re-voicing that clip
(`.claude/rules/learn-content.md`).

Category 1 (pure): no network, no Supabase.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from app.services.learn_weekly import validators as V

BACKEND = Path(__file__).resolve().parents[1]
MONEY_MOVES = BACKEND / "data" / "money_moves.json"

#: slug → the validator codes its blocks hit. Articles with no hit are absent
#: (`nvidias-ai-dominance`, `the-home-depot-vs-lowes`).
OWNER_FIX_LIST = {
    "amd-vs-intel-the-cpu-wars": {"person_named"},
    "apples-services-revolution": {"block_type", "word_limit"},
    "boeing-vs-airbus-the-aerospace-duopoly": {"word_limit"},
    "costcos-membership-magic": {"class_b_recommendation", "time_word", "word_limit"},
    "google-vs-microsoft-ai-wars": {"word_limit"},
    "how-amazon-built-its-moat": {"block_type", "class_b_recommendation", "person_named",
                                  "time_word", "word_limit"},
    "metas-metaverse-pivot": {"class_b_recommendation", "word_limit"},
    "microsofts-cloud-metamorphosis": {"person_named"},
    "netflix-vs-disney-plus": {"time_word", "word_limit"},
    "tesla-vs-traditional-auto": {"word_limit"},
    "the-fall-of-enron": {"code_owned", "conduct_claim", "time_word", "word_limit"},
    "the-fall-of-sears": {"block_type", "class_b_recommendation", "famous_quote", "word_limit"},
    "the-ftx-collapse": {"conduct_claim", "person_named", "word_limit"},
    "the-future-of-digital-finance": {"banned_phrase", "block_type", "code_owned"},
    "the-rise-of-lvmh": {"class_b_recommendation", "person_named", "time_word", "word_limit"},
    "the-rise-of-tiktok-vs-instagram-reels": {"link"},
    "theranos-blood-and-lies": {"conduct_claim", "person_named", "word_limit"},
    "tsmc-the-foundry-that-runs-the-world": {"person_named"},
    "visa-vs-mastercard": {"word_limit"},
    "warren-buffetts-early-days": {"advice_directive", "block_type", "class_b_recommendation",
                                   "famous_quote", "misquote", "person_named", "word_limit"},
    "weworks-unraveling": {"word_limit"},
}

#: The owner-actionable advice wording, phrase by phrase (the plan names these as known hits).
ADVICE_PHRASES = {
    "costcos-membership-magic": {"buy low"},
    "how-amazon-built-its-moat": {"worth owning"},
    "metas-metaverse-pivot": {"should own"},
    "the-fall-of-sears": {"buy when", "hold the shares"},
    "the-rise-of-lvmh": {"buy it"},
    "warren-buffetts-early-days": {"buy when"},
}

#: The trade-verb / reader's-holdings rows (review F2, 2026-10-23), detail by detail: the bundle's
#: one hit is the Mr. Market parable. A new detail here is either a real catch or over-blocking.
DIRECTIVE_DETAILS = {
    "warren-buffetts-early-days": {"buy … shares", "your shares"},
}

#: Codes that are weekly-only SHAPE rules (category 2 above) — never an owner fix by themselves.
WEEKLY_ONLY_CODES = {"word_limit", "block_type", "person_named", "conduct_claim"}


def _run():
    doc = json.loads(MONEY_MOVES.read_text(encoding="utf-8"))
    articles = doc["articles"]
    hits = defaultdict(set)
    details = defaultdict(set)
    blocks = 0
    for a in articles:
        for i, section in enumerate(a["sections"]):
            for j, block in enumerate(section["content"]):
                blocks += 1
                for issue in V.validate_block(f"sections[{i}].content[{j}]", block,
                                              section_kind="history"):
                    hits[a["slug"]].add(issue.code)
                    details[(a["slug"], issue.code)].add(issue.detail)
    return articles, blocks, dict(hits), details


def test_the_corpus_run_matches_the_owner_fix_list():
    started = time.perf_counter()
    articles, blocks, hits, details = _run()
    elapsed = time.perf_counter() - started
    # Anti-vacuity: the whole bundle was read.
    assert len(articles) == 23, len(articles)
    assert blocks > 300, blocks
    assert elapsed < 30.0, f"validators took {elapsed:.1f}s over the bundle"

    if hits != OWNER_FIX_LIST:
        lines = []
        for slug in sorted(set(hits) | set(OWNER_FIX_LIST)):
            got, want = hits.get(slug, set()), OWNER_FIX_LIST.get(slug, set())
            for code in sorted(got - want):
                lines.append(f"  NEW   {slug}: {code} {sorted(details[(slug, code)])[:4]}")
            for code in sorted(want - got):
                lines.append(f"  GONE  {slug}: {code}")
        raise AssertionError(
            "the bundle's validator hits changed — update OWNER_FIX_LIST (a fixed item leaves "
            "the list; a NEW one is either a real catch for the owner or validator "
            "over-blocking to fix in validators.py):\n" + "\n".join(lines))


def test_no_block_crashes_the_validators():
    _articles, _blocks, hits, _details = _run()
    assert not any("validator_error" in codes for codes in hits.values()), hits


def test_the_advice_wording_is_pinned_phrase_by_phrase():
    _articles, _blocks, _hits, details = _run()
    got = {slug: phrases for (slug, code), phrases in details.items()
           if code == "class_b_recommendation"}
    assert got == ADVICE_PHRASES


def test_the_trade_and_holdings_rows_are_pinned_detail_by_detail():
    _articles, _blocks, _hits, details = _run()
    got = {slug: d for (slug, code), d in details.items() if code == "advice_directive"}
    assert got == DIRECTIVE_DETAILS


def test_the_two_categories_cover_the_list():
    """Every code on the list is either owner-actionable wording or a weekly-only shape rule —
    the docstring's reading of the list stays true."""
    actionable = {"class_b_recommendation", "banned_phrase", "code_owned", "famous_quote",
                  "misquote", "time_word", "link", "advice_directive"}
    used = set().union(*OWNER_FIX_LIST.values())
    assert used <= actionable | WEEKLY_ONLY_CODES, used - (actionable | WEEKLY_ONLY_CODES)
    # No first-person self-reference, quotation with a speaker, valuation verdict or authorship
    # line anywhere in the hand-made corpus: those rules must never have needed a bundle exception
    # (the bundle's four framing quotes — "the challenger won" — are the quotation rule's twins).
    assert not used & {"self_reference", "suitability_claim", "rumour", "quotation",
                       "valuation_verdict", "authorship_reveal", "forbidden_token", "empty",
                       "block_invalid", "callout_style", "icon"}


def test_the_chat_style_advice_rows_fire_nowhere_in_the_bundle():
    """`advice_directive` is on the list only through the Learn trade/holdings rows; the copied
    chat-guardrail phrases ("you should buy") never needed a bundle exception."""
    doc = json.loads(MONEY_MOVES.read_text(encoding="utf-8"))
    texts = [t for a in doc["articles"] for sec in a["sections"] for b in sec["content"]
             for t in ([b.get("text")] + list(b.get("items") or [])) if isinstance(t, str)]
    assert len(texts) > 300
    assert [t for t in texts if V.advice_hits(t)] == []
