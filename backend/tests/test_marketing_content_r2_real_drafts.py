"""
Real writer output as a false-positive guard (round-2 bypass fix).

Every rule the round-2 bypass fix added is structural (clause scope, a lexicon bound to a company
or instrument mention, a role in ROLE position), but each was also run against what the model
ACTUALLY writes: `tests/data/marketing_real_draft_honest_texts.json` holds every field of the
accepted items from a real `marketing_preview --raw` run that passed the full field scan when it
was vendored (1,224 texts over 29 items). A new rule that rejects one of them over-blocks honest
copy the model produces on its own — narrow the rule, do not edit the fixture.

The fixture is model output, not derived from any lexicon under test — but it was SCAN-selected
(round 3, W3VAC-02): "honest" meant "the scan passed it", so a scan false negative could sit in
it and block the rule that closes it. Four Mr. Market lines that time a trade ("When his greed
makes prices high, you can choose to pass or sell", "His fear can present opportunities to buy")
were exactly that; they now sit in `must_reject`, each with its reason, and are asserted
rejected below. Reclassify a policy-breaking line with a why; never reclassify one to make a test
pass. The anti-vacuity test pins that the fixture is non-trivial and that the scan it runs is the
production one (`writer_service._scan`).

Category 1 (pure): the Learn bundle, the vendored lists and one JSON fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from app.services.marketing import content_pool
from app.services.marketing import writer_service as ws

FIXTURE = Path(__file__).resolve().parent / "data" / "marketing_real_draft_honest_texts.json"


def _cases() -> List[Tuple[str, str, bool]]:
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    out: List[Tuple[str, str, bool]] = []
    for key, slot in sorted(doc["items"].items()):
        out += [(key, t, False) for t in slot["shared"]]
        out += [(key, t, True) for t in slot["captions"]]
    return out


_CASES = _cases()


def test_the_fixture_is_real_and_non_trivial():
    keys = {k for k, _t, _e in _CASES}
    assert len(_CASES) >= 1000 and len(keys) >= 25
    # Both kinds, and the kinds of text the round-2 rules are closest to.
    assert any(k.startswith("journey:") for k in keys) and any(k.startswith("money_moves:")
                                                              for k in keys)
    joined = " ".join(t for _k, t, _e in _CASES)
    for probe in ("Myth:", "Many believe", "Mr. Market", "owner", "portfolio", "Costco",
                  "never forced", "if you"):
        assert probe in joined, probe


def test_every_item_in_the_fixture_is_still_in_the_pool():
    missing = sorted({k for k, _t, _e in _CASES if content_pool.get_item(k) is None})
    assert missing == [], missing


def _by_item() -> Dict[str, List[Tuple[str, bool]]]:
    out: Dict[str, List[Tuple[str, bool]]] = {}
    for key, text, emoji in _CASES:
        out.setdefault(key, []).append((text, emoji))
    return out


@pytest.mark.parametrize("key", sorted(_by_item()))
def test_honest_real_model_output_still_passes_the_field_scan(key):
    item = content_pool.get_item(key)
    assert item is not None, key
    failures = []
    for text, emoji in _by_item()[key]:
        vs = ws._scan("f", text, item, allow_emoji=emoji)
        if vs:
            failures.append((text[:120], [(v.code, v.detail) for v in vs]))
    assert failures == [], (key, failures)


# ── round 3 (W3VAC-02): lines the scan once passed that break rule 3 ─────────────────────────


def _must_reject() -> List[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["must_reject"]


def test_the_reclassified_lines_are_out_of_the_honest_set_and_carry_a_reason():
    rejected = _must_reject()
    assert len(rejected) >= 4 and all(r["why"] and r["codes"] for r in rejected)
    honest = {t for _k, t, _e in _CASES}
    assert not honest & {r["text"] for r in rejected}
    # The neutral repairs the model wrote for the same lines stay honest (the over-block guard).
    assert "When his fear makes prices low, you can consider the situation." in honest
    assert "His fear can present opportunities for consideration." in honest


@pytest.mark.parametrize("case", _must_reject(), ids=lambda r: r["text"][:40])
def test_a_reclassified_timed_trade_is_rejected(case):
    item = content_pool.get_item(case["item"])
    got = {v.code for v in ws._scan("f", case["text"], item, allow_emoji=case["emoji"])}
    assert set(case["codes"]) <= got, (case["text"], got)
