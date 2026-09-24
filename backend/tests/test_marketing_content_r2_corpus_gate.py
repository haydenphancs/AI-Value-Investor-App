"""
The round-2 CORPUS GATE: what a real writer run produced, classified by hand.

`tests/data/marketing_real_drafts_2026_09_24.json` holds every field of every round (draft AND
repair) of all 35 items of one real gemini-2.5-flash run — the accepted items and the six the
gate REJECTED — split into:

* `honest` — copy that breaks no policy rule. Before round 2's over-block fix, 30 distinct
  fields of it were rejected: restated dates and prices with the writer's own verb ("Amazon
  started as an online bookstore in 1994", "NVIDIA acquired Mellanox for approximately 6.9
  billion dollars"), a lesson's own titles ("Market Cap", "P/E Ratio"), "buy high and sell low"
  as the MISTAKE a lesson names, "Beware of "Guaranteed Returns"", Apple's App Store as Apple's
  product, "a compelling case study", "earnings signals expansion". Every one must now pass the
  production field scan (`writer_service._scan`: compliance + grounding).
* `true_positives` — the only fields that DO break a rule, each with the code that must still
  reject it.

The fixture is model output, not derived from any lexicon under test;
`test_the_fixture_is_the_whole_run` pins that it covers the rejected items and the exact shapes
the round-2 rules were narrowed for. It is NOT hand-classified line by line (round 3, W3VAC-02): only the scan's
REJECTIONS were classified when it was vendored, and everything the scan passed became `honest`
— so a scan false negative could be pinned as honest copy. Four Mr. Market lines that time a
trade ("…you can choose to pass or sell", "His fear can present opportunities to buy") were; they
are now `true_positives`, each with its reason. Narrow a rule that rejects an honest line; move a
line that breaks a policy rule to `true_positives` with a why — never edit the fixture to make a
test pass.

Category 1 (pure): the Learn bundle, the vendored lists and one JSON fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from app.services.marketing import content_pool
from app.services.marketing import writer_service as ws

FIXTURE = Path(__file__).resolve().parent / "data" / "marketing_real_drafts_2026_09_24.json"
_DOC = json.loads(FIXTURE.read_text(encoding="utf-8"))

#: The items the gate REJECTED in the real run: their fields are the over-blocked ones.
_REJECTED_IN_THE_RUN = (
    "journey:fomo_cycle", "journey:key_statistics", "journey:risk_reward",
    "money_moves:apples-services-revolution", "money_moves:boeing-vs-airbus-the-aerospace-duopoly",
    "money_moves:the-rise-of-lvmh",
)
#: One honest line per over-block the round-2 fix removed (the shapes the rules were narrowed
#: for). If one disappears from the fixture the gate no longer proves that fix.
_OVERBLOCKED_BEFORE = (
    "Amazon started as an online bookstore in 1994.",
    "CUDA, launched in 2006, provided the tools needed for AI researchers.",
    "NVIDIA acquired Mellanox for approximately 6.9 billion dollars.",
    "Examples include Dior, Fendi, Celine, and Tiffany, acquired for about $15.8 billion in 2021.",
    "The group acquires brands with established names and rich histories, like Bulgari in 2011.",
    "The 737 MAX Example",
    "Market Cap",
    "P/E Ratio",
    "An emotional loop that can lead investors to buy high and sell low.",
    'Beware of "Guaranteed Returns"',
    "Apple focused on the App Store, iCloud, and various subscriptions.",
    "Tesla focused on electric vehicles 10 years before incumbents.",
)


def _honest() -> List[Tuple[str, str, str, bool, bool]]:
    return [tuple(h) for h in _DOC["honest"]]


def test_the_fixture_is_the_whole_run():
    honest = _honest()
    keys = {h[0] for h in honest}
    assert len(honest) >= 1500 and len(keys) == 35, (len(honest), len(keys))
    assert set(_REJECTED_IN_THE_RUN) <= keys
    texts = {h[2] for h in honest}
    missing = [t for t in _OVERBLOCKED_BEFORE if not any(t in x for x in texts)]
    assert missing == [], missing
    # Every kind of field is represented, and captions with emoji allowed. NOT a myth-titled body:
    # no real card or slide title in this run matched `writer_service._MYTH_TITLE_RE`, so no
    # honest row carries myth_framed=True (W3VAC-05) — the title path is pinned by
    # test_marketing_content_r2_overblock.py and test_marketing_content_r3_frames.py, never here.
    assert not any(h[4] for h in honest), "a real myth-titled row: pin it and fix this comment"
    fields = {h[1] for h in honest}
    assert {"hook", "video_script", "cards.title", "cards.body", "carousel_slides.title",
            "carousel_slides.body", "x", "linkedin", "youtube_title"} <= fields
    assert _DOC["true_positives"], "a gate with nothing to reject proves nothing"


def test_every_item_in_the_fixture_is_still_eligible():
    keys = sorted({h[0] for h in _honest()} | {t["item"] for t in _DOC["true_positives"]})
    bad = [k for k in keys if not (content_pool.get_item(k) and content_pool.get_item(k).eligible)]
    assert bad == [], bad


def _by_item() -> Dict[str, List[Tuple[str, str, bool, bool]]]:
    out: Dict[str, List[Tuple[str, str, bool, bool]]] = {}
    for key, field, text, emoji, myth in _honest():
        out.setdefault(key, []).append((field, text, emoji, myth))
    return out


@pytest.mark.parametrize("key", sorted(_by_item()))
def test_honest_real_model_output_passes_the_field_scan(key):
    item = content_pool.get_item(key)
    failures = []
    for field, text, emoji, myth in _by_item()[key]:
        vs = ws._scan(field, text, item, allow_emoji=emoji, myth_framed=myth)
        if vs:
            failures.append((field, text[:140], [(v.code, v.detail) for v in vs]))
    assert failures == [], (key, failures)


@pytest.mark.parametrize("case", _DOC["true_positives"], ids=lambda c: c["item"])
def test_the_true_positives_of_the_run_are_still_rejected(case):
    item = content_pool.get_item(case["item"])
    got = {v.code for v in ws._scan(case["field"], case["text"], item, allow_emoji=case["emoji"],
                                    myth_framed=case["myth_framed"])}
    assert set(case["codes"]) <= got, (case["text"], got)
