"""
The writer's LENGTH contract (2026-09-26): what the prompt asks for, and what a repair is told.

Measured on a real gemini-2.5-flash run (2026-09-24): every rejected generation failed on the
script's word ceiling (the model wrote up to 179 words when asked for "90 to 140"), and X
bodies overshot their exact budget by 1-30 characters (the prompt assumed 6.2 characters a word;
the model wrote 6.47). The fixes are pinned here:

* the script is asked for STRUCTURALLY (a line count and a per-line cap), and the obeyed maximum
  sits under the enforced ceiling by construction;
* every caption is asked for a fraction of its enforced budget, with a measured
  characters-per-word figure, and never more characters than the budget;
* a length violation's detail — which the repair prompt replays verbatim — names the hard limit
  and how much to cut, once (it used to show two different ceilings for one caption).

Category 1 (pure): the Learn bundle and the prompt builders; no model call.
"""

from __future__ import annotations

import copy
import re
from datetime import date

import pytest

from app.services.marketing import content_pool
from app.services.marketing import post_copy
from app.services.marketing import writer_prompts as wp
from app.services.marketing import writer_service as ws
from app.services.marketing.selection import TEMPLATES_BY_ID

RUN_DATE = date(2026, 9, 21)
TEMPLATE = TEMPLATES_BY_ID["three_takeaways"]
ITEM_KEY = "journey:mr_market"


def _item() -> content_pool.ContentItem:
    item = content_pool.get_item(ITEM_KEY)
    assert item is not None and item.eligible
    return item


def _spec() -> str:
    return wp._output_spec(_item(), RUN_DATE, False)


# ── the script: structural ask, obeyed maximum under the ceiling ─────────────────────────────


def test_the_obeyed_script_maximum_is_under_the_enforced_ceiling():
    obeyed_max = wp._ASK_SCRIPT_LINES[1] * wp._ASK_SCRIPT_LINE_WORDS
    assert obeyed_max < wp.SCRIPT_MAX_WORDS, (obeyed_max, wp.SCRIPT_MAX_WORDS)
    assert wp._ASK_SCRIPT_TOTAL_WORDS <= obeyed_max
    # …and the obeyed MINIMUM sits over the enforced floor (the first ask let lines run short).
    obeyed_min = wp._ASK_SCRIPT_LINES[0] * wp._ASK_SCRIPT_LINE_WORDS_MIN
    assert obeyed_min > wp.SCRIPT_MIN_WORDS, (obeyed_min, wp.SCRIPT_MIN_WORDS)
    assert wp.SCRIPT_MIN_LINES <= wp._ASK_SCRIPT_LINES[0] <= wp._ASK_SCRIPT_LINES[1] <= wp.SCRIPT_MAX_LINES
    assert wp._ASK_SCRIPT_LINE_WORDS < wp.SCRIPT_LINE_MAX_WORDS
    # The obeyed minimum (6 lines of a few words) is the model's to meet; the ask's TOTAL is well
    # above the enforced floor.
    assert wp._ASK_SCRIPT_TOTAL_WORDS > wp.SCRIPT_MIN_WORDS


def test_the_prompt_states_the_line_count_and_the_per_line_cap():
    spec = _spec()
    line = next(x for x in spec.splitlines() if x.startswith("- video_script:"))
    assert f"{wp._ASK_SCRIPT_LINES[0]} to {wp._ASK_SCRIPT_LINES[1]} lines" in line
    assert f"{wp._ASK_SCRIPT_LINE_WORDS_MIN} to {wp._ASK_SCRIPT_LINE_WORDS} words" in line
    assert f"about {wp._ASK_SCRIPT_TOTAL_WORDS} words in total" in line
    assert "ONE sentence" in line


# ── captions: a fraction of the enforced budget, never over it ────────────────────────────────


@pytest.mark.parametrize("category", ["blueprints", "battles", "valueTraps", "foundation", "analysis",
                                      "strategies", "mastery", "unknown"])
@pytest.mark.parametrize("allow_x_url", [False, True])
def test_every_caption_ask_is_under_its_enforced_budget(category, allow_x_url):
    for f in post_copy.CAPTION_FIELDS:
        limit = post_copy.body_budget(f, category, RUN_DATE, allow_x_url=allow_x_url)
        words, chars = wp.caption_target(f, limit)
        assert chars < limit, (f, chars, limit)
        # The word ask at the MEASURED rate (6.47 chars/word) stays inside the character ask.
        assert words * 6.47 <= chars + 6.47, (f, words, chars)
        ratio = (wp._BUDGET_TARGET_COMPUTED if f in post_copy.COMPUTED_BUDGET_FIELDS
                 else wp._BUDGET_TARGET_LONG)
        assert chars == int(limit * ratio)


def test_the_x_ask_is_tighter_than_the_measured_overshoot():
    """At the old ratio X was asked for 159 characters of a 199 budget; the model wrote 216."""
    limit = post_copy.body_budget("x", "blueprints", RUN_DATE, allow_x_url=False)
    words, chars = wp.caption_target("x", limit)
    assert chars <= int(limit * 0.75) and words <= 23, (words, chars, limit)


def test_the_x_and_bluesky_lines_say_no_emoji_and_the_others_do_not():
    spec = _spec()
    for f in post_copy.CAPTION_FIELDS:
        line = next(x for x in spec.splitlines() if x.startswith(f"  - {f}:"))
        assert ("no emoji" in line) == (f in ("x", "bluesky")), line
    # The existing pins still hold.
    assert "youtube_title: about" in spec
    assert "no < or > characters, one line" in spec


# ── repair details: the hard limit and the cut, once ──────────────────────────────────────────


def _words_of(n: int) -> str:
    base = " ".join(s for s in _item().fact_sentences).split()
    return " ".join(base[i % len(base)] for i in range(n))


def _pkg() -> dict:
    # The writer tests' clean package, rebuilt here to keep this file self-contained.
    item = _item()
    g = [s for s in item.fact_sentences
         if s.endswith(".") and 6 <= len(s.split()) <= 20 and "Mr." not in s
         and "bargain" not in s and "buy" not in s.lower().split()[-1]]
    return {
        "hook": min(g[:6], key=lambda s: len(s.split())),
        "video_script": g[:8],
        "cards": [{"title": " ".join(g[i].split()[:5]).rstrip(",."), "body": g[i + 1]}
                  for i in (0, 2, 4)],
        "carousel_slides": [{"title": " ".join(g[i].split()[:6]).rstrip(",."),
                             "body": f"{g[i + 1]} {g[i + 2]}"} for i in range(0, 10, 2)],
        "captions": {"tiktok": " ".join(g[0:3]), "youtube_title": g[1],
                     "youtube_description": " ".join(g[2:6]), "instagram": " ".join(g[3:6]),
                     "facebook": " ".join(g[4:7]), "x": g[5], "threads": " ".join(g[6:8]),
                     "bluesky": g[7], "linkedin": " ".join(g[:5])},
    }


def _detail(pkg: dict, field: str, code: str) -> str:
    vr = ws.validate_package(pkg, _item(), RUN_DATE)
    hits = [v.detail for v in vr.violations if v.field == field and v.code == code]
    assert hits, [(v.field, v.code, v.detail) for v in vr.violations]
    return hits[0]


def test_an_overlong_script_names_the_limit_and_the_cut():
    pkg = copy.deepcopy(_pkg())
    n = wp.SCRIPT_MAX_WORDS + 6
    per = n // 8
    pkg["video_script"] = [_words_of(per) + "." for _ in range(7)] + [_words_of(n - 7 * per) + "."]
    d = _detail(pkg, "video_script", "length")
    assert d.startswith(f"{n} words") and f"limit is {wp.SCRIPT_MAX_WORDS}" in d, d
    assert "cut at least 6 words" in d and "drop a line" in d, d


def test_a_short_script_names_the_minimum_and_how_much_to_add():
    pkg = copy.deepcopy(_pkg())
    pkg["video_script"] = [_words_of(10) + "." for _ in range(5)]
    d = _detail(pkg, "video_script", "length")
    assert f"minimum is {wp.SCRIPT_MIN_WORDS}" in d and f"add at least {wp.SCRIPT_MIN_WORDS - 50}" in d
    assert "add a whole line" in d


def test_an_overlong_hook_and_line_name_their_maximum():
    pkg = copy.deepcopy(_pkg())
    pkg["hook"] = _words_of(wp.HOOK_MAX_WORDS + 3)
    d = _detail(pkg, "hook", "too_long")
    assert f"limit is {wp.HOOK_MAX_WORDS}" in d and "cut at least 3 words" in d, d
    pkg = copy.deepcopy(_pkg())
    pkg["video_script"] = [_words_of(wp.SCRIPT_LINE_MAX_WORDS + 2)] + pkg["video_script"][1:]
    d = _detail(pkg, "video_script[0]", "too_long")
    assert f"limit is {wp.SCRIPT_LINE_MAX_WORDS}" in d and "cut at least 2 words" in d, d


def test_an_overlong_x_body_names_one_ceiling_and_the_cut():
    pkg = copy.deepcopy(_pkg())
    budget = post_copy.body_budget("x", _item().category, RUN_DATE, allow_x_url=False)
    body = _words_of(60)
    while post_copy.measured_length("x", body) <= budget + 16:
        body += " more"
    n = post_copy.measured_length("x", body)
    pkg["captions"]["x"] = body
    d = _detail(pkg, "x", "too_long")
    assert f"hard limit is {budget}" in d and f"cut at least {n - budget} characters" in d, d
    # ONE ceiling: the enforced budget, never the prompt's smaller ask.
    ask_chars = wp.caption_target("x", budget)[1]
    assert ask_chars not in {int(x) for x in re.findall(r"\d+", d)} - {n, budget, n - budget}, d
    assert d.count("limit") == 1, d


def test_the_repair_prompt_replays_the_actionable_detail():
    pkg = copy.deepcopy(_pkg())
    pkg["hook"] = _words_of(wp.HOOK_MAX_WORDS + 3)
    vr = ws.validate_package(pkg, _item(), RUN_DATE)
    prompt = wp.repair_prompt(_item(), TEMPLATE, RUN_DATE, generation_id="g1", round_no=2,
                              previous=pkg, violations=vr.violations)
    assert "cut at least 3 words" in prompt
    assert wp.REPAIR_HINTS["too_long"] in prompt


def test_the_length_hint_serves_both_directions():
    # 2026-09-26: the hint read "cut (or add) … drop a whole line"; a model holding a 58-word
    # script (minimum 60) returned it byte-identical. The hint must name the ADD path too.
    hint = wp.REPAIR_HINTS["length"]
    assert "if it says add" in hint and "if it says cut" in hint
    assert "one more" in hint


def test_a_narrated_word_longer_than_the_audio_table_allows_is_refused_as_content():
    """Review 2026-09-26: a 50-character chained token passed every writer check, then the
    server's audio timing table (AUDIO_WORD_MAX_CHARS) refused it on every voice attempt."""
    from app.schemas.marketing import AUDIO_WORD_MAX_CHARS

    token = "\u201c" + "-".join(["buy", "high", "sell", "low", "then", "wonder", "why", "it",
                                "never", "works"]) + "\u201d"
    assert len(token) > AUDIO_WORD_MAX_CHARS
    pkg = _pkg()
    pkg["video_script"][2] = f"Skip the {token} habit and think like an owner."
    d = _detail(pkg, "video_script[2]", "too_long")
    assert f"limit is {AUDIO_WORD_MAX_CHARS}" in d and f"{len(token)} characters" in d, d
    # The hook is narrated too.
    pkg = _pkg()
    pkg["hook"] = f"Why is {token} a trap?"
    assert f"limit is {AUDIO_WORD_MAX_CHARS}" in _detail(pkg, "hook", "too_long")
    # Exactly at the limit is fine (the table allows 1-48 characters).
    pkg = _pkg()
    pkg["video_script"][2] = "Think about " + "a" * (AUDIO_WORD_MAX_CHARS - 1) + "."
    vr = ws.validate_package(pkg, _item(), RUN_DATE)
    assert not any(f"limit is {AUDIO_WORD_MAX_CHARS}" in v.detail for v in vr.violations)
