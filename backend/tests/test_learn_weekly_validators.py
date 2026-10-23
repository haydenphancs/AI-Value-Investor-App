"""In-app validators for Wiser weekly pieces (`app/services/learn_weekly/validators.py`) and the
shared Learn content rules (`app/services/learn_content_rules.py`).

Plan "Wiser weekly content engine" §1.7 "Validators" / §1.12. Category 1 (pure): no network, no
Supabase — the grounding vocabulary is read from the bundled Learn JSON on disk, exactly as the
marketing writer reads it.

Hardening (CLAUDE.md): every rule is tested on BOTH sides (a must-fail and a must-pass twin), and
the outliers — empty, None, wrong types, bool-as-int, NaN, huge strings and lists, duplicates,
zero-width and homoglyph tricks — assert the correct DEGRADED answer (an issue, never a crash and
never a silent pass).
"""

from __future__ import annotations

import ast
import json
import logging
import math
import subprocess
import sys
import time
from pathlib import Path
from types import MappingProxyType

import pytest

from app.services import learn_content_rules as rules
from app.services.learn_weekly import contracts as C
from app.services.learn_weekly import validators as V
from app.services.marketing import compliance, content_pool, grounding

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
IOS_MONEY_MOVES = REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
VALIDATORS_PY = BACKEND / "app/services/learn_weekly/validators.py"
RULES_PY = BACKEND / "app/services/learn_content_rules.py"


def codes(issues):
    return [i.code for i in issues]


def para(text, kind="history", **kw):
    return V.validate_block("b", {"type": "paragraph", "text": text}, section_kind=kind, **kw)


def para_codes(text, kind="history", **kw):
    return codes(para(text, kind, **kw))


# ═════════════════════════════════════════════════════════════════════════════════════════════
# A. learn_content_rules
# ═════════════════════════════════════════════════════════════════════════════════════════════


def _bundle_icons(node):
    """Every value under an `icon` key anywhere in the document (keyHighlights, section icons,
    callout icons — and any future place a symbol name is stored)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "icon" and isinstance(v, str):
                yield v
            else:
                yield from _bundle_icons(v)
    elif isinstance(node, list):
        for v in node:
            yield from _bundle_icons(v)


def test_icon_allowlist_equals_what_the_bundle_draws():
    """`test_ios_symbol_availability.py` proves every bundle icon exists at the deployment target.
    A weekly piece may draw only `ICON_ALLOWLIST`, so pinning it EQUAL to the bundle's set carries
    that guarantee over (a too-new symbol draws nothing on an older iPhone, silently)."""
    doc = json.loads(IOS_MONEY_MOVES.read_text(encoding="utf-8"))
    used = set(_bundle_icons(doc))
    assert len(used) > 50, f"only {len(used)} icons found — the walk would be vacuous"
    missing = used - rules.ICON_ALLOWLIST
    extra = rules.ICON_ALLOWLIST - used
    assert not missing and not extra, (
        f"ICON_ALLOWLIST drifted from the bundle: add {sorted(missing)}, remove {sorted(extra)}")


def test_icon_allowlist_is_a_frozenset_of_symbol_names():
    assert isinstance(rules.ICON_ALLOWLIST, frozenset)
    assert all(isinstance(s, str) and s and s == s.strip() and " " not in s
               for s in rules.ICON_ALLOWLIST)


def test_rule_lists_are_read_only_and_pre_normalised():
    assert isinstance(rules.BANNED_PHRASES, tuple)
    assert isinstance(rules.ATTRIBUTION_REQUIRED, MappingProxyType)
    with pytest.raises(TypeError):
        rules.ATTRIBUTION_REQUIRED["new phrase"] = ("someone",)   # type: ignore[index]
    for phrase in tuple(rules.BANNED_PHRASES) + tuple(rules.ATTRIBUTION_REQUIRED):
        assert rules.norm(phrase) == phrase


def test_norm_folds_punctuation_case_and_curly_apostrophes():
    assert rules.norm("In the Short-Run,  the MARKET is a voting machine!") == (
        "in the short run the market is a voting machine")
    assert rules.norm("Don’t") == rules.norm("Don't") == "dont"
    assert rules.norm("") == ""


def test_rules_module_is_stdlib_only():
    """The rules are read by tests, seeders and the weekly engine alike: no app import."""
    tree = ast.parse(RULES_PY.read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert mods <= {"__future__", "re", "unicodedata", "types", "typing"}, mods


# ═════════════════════════════════════════════════════════════════════════════════════════════
# B. The enforced compliance codes, and parity with the code they were copied from
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_enforced_compliance_codes_are_pinned_exactly():
    assert V.ENFORCED_COMPLIANCE_CODES == frozenset({
        "class_b_recommendation", "class_b_forward", "promissory", "banned_phrase",
        "misattribution", "famous_quote", "link", "markup", "emoji", "handle", "hashtag",
        "cashtag", "code_owned", "brand_mention", "person_named",
    })
    # The plan's named exclusions stay excluded.
    for code in ("class_b_valuation", "return_figure", "identity_leak"):
        assert code not in V.ENFORCED_COMPLIANCE_CODES


def _compliance_codes():
    """Every code `compliance.scan_text` can emit, read from its source (literal codes in
    `scan_text`/`_sentence_hits` plus the `class_b_<code>` families of its row tables)."""
    import inspect
    import re

    scan = inspect.getsource(compliance.scan_text)
    sh = inspect.getsource(compliance._sentence_hits)
    out = set(re.findall(r'\bv\(\s*"([a-z_]+)"', scan))
    out |= set(re.findall(r'_RE, "([a-z_]+)"\)', scan))
    out |= set(re.findall(r'add\(\s*"([a-z_]+)"', sh))
    out |= set(re.findall(r'out\.append\(\(\s*"([a-z_]+)"', sh))
    out |= {f"class_b_{code}" for code, *_rest in compliance.CLASS_B_TIER1}
    out |= {f"class_b_{code}" for code, _p in compliance._COMPANY_ROWS}
    return out


def test_every_compliance_code_is_a_deliberate_decision():
    """A new marketing rule must be decided in-app (enforced or ignored), never inherited
    silently — and the two sets never overlap."""
    emitted = _compliance_codes()
    for anchor in ("banned_phrase", "class_b_valuation", "person_named", "promissory", "link"):
        assert anchor in emitted, f"the source scan missed {anchor!r} — it would be vacuous"
    assert not (V.ENFORCED_COMPLIANCE_CODES & V.IGNORED_COMPLIANCE_CODES)
    undecided = emitted - (V.ENFORCED_COMPLIANCE_CODES | V.IGNORED_COMPLIANCE_CODES)
    assert not undecided, f"compliance emits codes the Learn validators never decided: {undecided}"
    stale = (V.ENFORCED_COMPLIANCE_CODES | V.IGNORED_COMPLIANCE_CODES) - emitted
    assert not stale, f"decided codes compliance no longer emits: {stale}"


TRAINED_ROW = r"\b(?:trained|fine-?tuned)(?:\s+\w+){0,3}\s+by (?:google|openai|gemini)\b"


def test_self_reference_rows_are_the_guardrail_rows_minus_the_unanchored_trained_row():
    from app.services.agents import chat_guardrails as cg

    theirs = set(cg._IDENTITY_ENFORCE_PATTERNS)
    ours = set(V._GUARDRAIL_SELF_PATTERNS)
    assert ours <= theirs, f"rows not copied verbatim: {ours - theirs}"
    assert theirs - ours == {TRAINED_ROW}, (
        "exactly the un-anchored trained-by row may be left out; got " + repr(theirs - ours))
    # Its own reason for exclusion, proven on the guardrail's regex.
    assert cg._IDENTITY_ENFORCE_RE.search("frontier models trained by openai run here")


def test_advice_and_suitability_rows_equal_the_guardrail_rows():
    from app.services.agents import chat_guardrails as cg

    assert V._ADVICE_PATTERNS == cg._ADVICE_PATTERNS
    assert V._SUITABILITY_PATTERNS == cg._SUITABILITY_PATTERNS


@pytest.mark.parametrize("text", [
    "You should buy this stock now.", "I'd recommend buying the dip.",
    "It is right for you.", "Given your risk tolerance, it fits.",
])
def test_advice_hits_agree_with_scan_answer(text):
    from app.services.agents import chat_guardrails as cg

    want = [t for t in cg.scan_answer(text) if t in ("advice_directive", "suitability_claim")]
    assert V.advice_hits(text) == want and want


def test_validators_never_import_the_agents_package_or_fmp():
    """`agents/__init__` loads the research agent and the FMP client: the validators copy the
    guardrail rows instead (module doc). AST over the source, then a fresh interpreter."""
    tree = ast.parse(VALIDATORS_PY.read_text(encoding="utf-8"))
    imported = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any("agents" in m or "fmp" in m or "integrations" in m for m in imported), imported
    probe = (
        "import sys; import app.services.learn_weekly.validators as v; "
        "bad = sorted(m for m in sys.modules if m.startswith(('app.services.agents', "
        "'app.integrations.fmp', 'app.integrations.gemini'))); print(bad); "
        "sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", probe], cwd=str(BACKEND), capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, f"validators pulled in {r.stdout.strip()} {r.stderr[-400:]}"


# ═════════════════════════════════════════════════════════════════════════════════════════════
# C. Identity: first-person self-reference only
# ═════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("text", [
    "Frontier models trained by OpenAI run on these clusters.",
    "LLMs need GPUs.",
    "AI model training demand rose.",
    "Inference is the steady work of answering questions.",
    "Anthropic and OpenAI rent large clusters of chips.",
    "Models fine-tuned by Google engineers serve search results.",
    "NVIDIA, as an AI chip maker, sells to every large cloud.",
    "As a language model grows, it needs more memory.",
    "Microsoft trained its own models on rented capacity.",
])
def test_third_person_ai_subject_matter_passes(text):
    assert V.self_reference_hits(text) == []
    assert para(text) == [], para(text)


@pytest.mark.parametrize("text", [
    "As an AI, I think this matters.",
    "As an AI language model, I cannot predict prices.",
    "I am an AI built to explain filings.",
    "I'm just an AI assistant.",
    "I was trained by Google.",
    "I have been fine-tuned on financial text.",
    "My training data ends in 2024.",
    "I am Gemini.",
    "I'm a large language model.",
    "I was developed by OpenAI.",
    "The model behind me reads filings.",
    "As an A\u200bI, I think this matters.",          # zero-width inside the phrase
    "AS AN AI, I THINK THIS MATTERS.",
    "As an \uff21\uff29, I think this matters.",        # fullwidth letters NFKC to "AI"
])
def test_first_person_self_reference_fails(text):
    assert V.self_reference_hits(text), text
    assert "self_reference" in para_codes(text)


def test_self_reference_helper_degrades_on_junk():
    for junk in (None, 5, b"As an AI, I", ["As an AI, I"], "", "   "):
        assert V.self_reference_hits(junk) == []
        assert V.advice_hits(junk) == []
        assert V.conduct_hits(junk) == []
        assert V.misquote_hits(junk) == []
        assert V.forbidden_token_hits(junk) == []


# ═════════════════════════════════════════════════════════════════════════════════════════════
# D. Advice, directives, promises and forecasts
# ═════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("text, code", [
    ("You should buy shares of the company now.", "advice_directive"),
    ("This kind of stock is right for you.", "suitability_claim"),
    ("Costco is worth owning.", "class_b_recommendation"),
    ("Buy low and hold the shares.", "class_b_recommendation"),
    ("Investors should own this stock.", "class_b_recommendation"),
    ("Consider buying the stock after a dip.", "class_b_recommendation"),
    ("The company will soar.", "class_b_forward"),
    ("NVIDIA is poised to keep winning.", "class_b_forward"),
    ("The market always recovers, so you can't lose.", "promissory"),
    ("These are guaranteed returns.", "banned_phrase"),
    ("This is not financial advice.", "banned_phrase"),
])
def test_directives_promises_and_forecasts_fail(text, code):
    assert code in para_codes(text), para(text)


@pytest.mark.parametrize("text", [
    "Some investors compare valuation with growth before they decide anything.",
    "A value trap looks cheap on paper while the business shrinks.",     # class_b_valuation: ignored
    "The market cap passed one trillion dollars in 2024.",               # valuation vocabulary
    "Revenue grew 40% in the fiscal year.",                              # return_figure: ignored
])
def test_ignored_compliance_rows_pass_in_app(text):
    assert para(text) == [], para(text)


# ═════════════════════════════════════════════════════════════════════════════════════════════
# E. People, rumour, time words
# ═════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("text", [
    "The CEO misled auditors about revenue.",
    "Management concealed losses for years.",
    "Executives inflated revenue before the sale.",
    "The founder lied to the board.",
    "Its chief financial officer falsified the ledger.",
    "The auditors covered up the gap.",
    "The company concealed losses.",
    "It was a fraud from the start.",
    "The firm committed securities fraud.",
    "Enron hid its debt in partnerships.",
    "Executives were charged with wire fraud.",
    "The board embezzled funds.",
])
def test_conduct_claims_fail(text):
    assert "conduct_claim" in para_codes(text), para(text)


@pytest.mark.parametrize("text", [
    "The CEO founded the company in a garage.",
    "Executives disclosed the contract in a filing.",
    "Demand inflated the backlog.",
    "TSMC fabricated the chips for its customers.",
    "Visa invested in fraud prevention.",
    "AI-powered fraud detection became a product line.",
    "Revenue growth alone can be misleading.",
    "Management raised its spending plan.",
    "The answer lies in the cost of power.",
])
def test_neutral_role_and_business_sentences_pass(text):
    assert "conduct_claim" not in para_codes(text), para(text)


def test_a_new_listing_no_lexicon_knows_is_caught_through_the_subjects():
    text = "CoreWeave misled its customers."
    assert "conduct_claim" not in para_codes(text)
    assert "conduct_claim" in para_codes(text, subjects=["CoreWeave, Inc.", "CRWV"])
    # Corporate suffixes and ordinary words never become subject tokens.
    assert V.subject_tokens(["CoreWeave, Inc.", "CRWV", "Advanced Micro Devices"]) == {
        "coreweave", "crwv"}
    for junk in (None, 5, "", [None, 7], ["Inc."]):
        assert V.subject_tokens(junk) == frozenset()


@pytest.mark.parametrize("text", [
    "The company is reportedly in talks to merge.",
    "The two firms are in talks.",
    "A rumor spread about a deal.",
    "A rumour spread about a deal.",
    "Sources say a deal is close.",
    "The supplier allegedly delayed shipments.",
])
def test_rumour_words_fail(text):
    assert "rumour" in para_codes(text), para(text)


@pytest.mark.parametrize("word", ["this week", "today", "recently", "this month", "yesterday",
                                  "last week"])
def test_time_words_fail_outside_a_peg(word):
    assert "time_word" in para_codes(f"The company filed a report {word}.")


@pytest.mark.parametrize("dated", [
    "This week, on October 21, 2026, the company disclosed a new contract in a filing.",
    "On 2026-10-21 the company disclosed a new contract, its largest this month.",
    "Today the company disclosed, in a filing dated October 21, 2026, a new contract.",
])
def test_time_words_pass_in_a_peg_block_with_a_full_date(dated):
    assert para(dated, "peg") == [], para(dated, "peg")
    # The same text outside a peg still fails: an evergreen section never dates itself.
    assert "time_word" in para_codes(dated, "history")


@pytest.mark.parametrize("undated", [
    "This week the company disclosed a new contract.",
    "This week, on October 2026, the company disclosed a new contract.",      # no day
    "This week, on February 30, 2026, the company disclosed a new contract.",  # no such day
    "This week, on 2026-13-01, the company disclosed a new contract.",         # no such month
])
def test_a_peg_without_a_real_full_date_still_fails(undated):
    assert "time_word" in para_codes(undated, "peg")


def test_the_date_must_be_in_the_same_block():
    """A bullet list is ONE block: the date in item 0 licenses the time word in item 1."""
    block = {"type": "bulletList", "items": ["The filing is dated October 21, 2026.",
                                             "This week it named a new customer."]}
    assert V.validate_block("b", block, section_kind="peg") == []
    block["items"] = ["The filing named a new customer.", "This week it named a second one."]
    assert "time_word" in codes(V.validate_block("b", block, section_kind="peg"))


def test_an_unknown_section_kind_is_reported_and_read_as_no_peg():
    out = para("This week, on October 21, 2026, the company filed.", kind="intro")
    assert {"section_kind", "time_word"} <= set(codes(out))
    for junk in (None, 5, "", "PEG"):
        assert "section_kind" in para_codes("A plain sentence.", kind=junk)


# ═════════════════════════════════════════════════════════════════════════════════════════════
# F. Forbidden tokens, misquotes, homoglyphs
# ═════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("text", [
    "Read **this** part.",
    "Read __this__ part.",
    "# A heading line",
    "## Another heading",
    "See https://example.com for more.",
    "See www.example.com for more.",
    "A rocket \U0001F680 took off.",
    "Caydex explains the filing.",
    "Cay AI explains the filing.",
    "Ca\u200bydex explains the filing.",              # zero-width space
    "Cay\u00addex explains the filing.",              # soft hyphen
    "C\u00e1ydex explains the filing.",               # accent
    "\uff23\uff41\uff59\uff44\uff45\uff58 explains the filing.",   # fullwidth
    "C\u0430ydex explains the filing.",               # Cyrillic \u0430 — non-Latin letter
    "Read *\u200b*this*\u200b* part.",                # zero-width inside the bold marker
    "Revenue rose \u0663 times.",                     # Arabic-Indic digit
])
def test_forbidden_tokens_fail_through_every_disguise(text):
    assert "forbidden_token" in para_codes(text), (text, para(text))


@pytest.mark.parametrize("phrase", list(rules.BANNED_PHRASES) + list(rules.ATTRIBUTION_REQUIRED))
def test_every_misquote_phrase_is_a_hard_refusal(phrase):
    assert "misquote" in para_codes(f"Some say {phrase} often.")


def test_a_correctly_credited_quote_is_still_refused_in_a_weekly_piece():
    text = "As Warren Buffett wrote, crediting Benjamin Graham: price is what you pay."
    got = set(para_codes(text))
    assert {"misquote", "person_named"} <= got, got


def test_misquote_matching_survives_punctuation_and_case():
    assert V.misquote_hits("In the SHORT-RUN, the market is a voting machine.") == [
        "short run the market is a voting machine"]


# ═════════════════════════════════════════════════════════════════════════════════════════════
# G. Block shape, word limits, caps
# ═════════════════════════════════════════════════════════════════════════════════════════════


def _words(n):
    return " ".join(["word"] * n) + "."


@pytest.mark.parametrize("btype, limit", [
    ("paragraph", C.MAX_PARAGRAPH_WORDS),
    ("subheading", C.MAX_SUBHEADING_WORDS),
])
def test_word_limit_boundaries(btype, limit):
    ok = V.validate_block("b", {"type": btype, "text": _words(limit)}, section_kind="history")
    assert "word_limit" not in codes(ok)
    over = V.validate_block("b", {"type": btype, "text": _words(limit + 1)}, section_kind="history")
    assert "word_limit" in codes(over)


def test_a_76_word_paragraph_fails():
    assert "word_limit" in para_codes(_words(76))
    assert "word_limit" not in para_codes(_words(75))


def test_callout_limits_style_and_icon():
    good = {"type": "callout", "style": "info", "icon": "lightbulb.fill",
            "text": _words(C.MAX_CALLOUT_WORDS)}
    assert V.validate_block("b", good, section_kind="lessons") == []
    assert "word_limit" in codes(V.validate_block(
        "b", {**good, "text": _words(C.MAX_CALLOUT_WORDS + 1)}, section_kind="lessons"))
    for style in ("loud", None, 3, "", "Info"):
        assert "callout_style" in codes(V.validate_block("b", {**good, "style": style},
                                                         section_kind="lessons"))
    for icon in ("sparkles.2", None, 7, "", "LIGHTBULB.FILL"):
        assert "icon" in codes(V.validate_block("b", {**good, "icon": icon}, section_kind="lessons"))
    missing = {k: v for k, v in good.items() if k != "icon"}
    assert "icon" in codes(V.validate_block("b", missing, section_kind="lessons"))


def test_bullet_list_limits():
    five = {"type": "bulletList", "items": [f"Point {w} matters here." for w in
                                            ("one", "two", "three", "four", "five")]}
    assert V.validate_block("b", five, section_kind="lessons") == []
    six = {"type": "bulletList", "items": five["items"] + ["Point six matters here."]}
    assert "word_limit" in codes(V.validate_block("b", six, section_kind="lessons"))
    long_item = {"type": "bulletList", "items": [_words(C.MAX_BULLET_WORDS + 1)]}
    out = V.validate_block("b", long_item, section_kind="lessons")
    assert Issue_at(out, "b.items[0]", "word_limit")
    huge = {"type": "bulletList", "items": ["Fine item."] * 100_000}
    started = time.perf_counter()
    assert "word_limit" in codes(V.validate_block("b", huge, section_kind="lessons"))
    assert time.perf_counter() - started < 2.0


def Issue_at(issues, field, code):
    return any(i.field == field and i.code == code for i in issues)


@pytest.mark.parametrize("items", [None, [], "a string", 5, {"a": 1}])
def test_bullet_list_without_a_usable_items_list(items):
    out = V.validate_block("b", {"type": "bulletList", "items": items}, section_kind="lessons")
    assert "block_invalid" in codes(out)


@pytest.mark.parametrize("bad", [5, None, b"x", ["x"], 1.5, True])
def test_non_string_items_and_texts_are_refused(bad):
    out = V.validate_block("b", {"type": "bulletList", "items": ["Fine.", bad]},
                           section_kind="lessons")
    assert Issue_at(out, "b.items[1]", "block_invalid"), out
    out = V.validate_block("b", {"type": "paragraph", "text": bad}, section_kind="lessons")
    assert "block_invalid" in codes(out)


def test_quote_blocks_are_refused():
    out = V.validate_block("b", {"type": "quote", "text": "Patience pays.",
                                 "attribution": "Someone"}, section_kind="history")
    assert codes(out) == ["block_type"]
    assert "quote" in out[0].detail


@pytest.mark.parametrize("block", [None, [], "paragraph", 5, 1.5, True, (), object()])
def test_non_dict_blocks_are_refused(block):
    out = V.validate_block("b", block, section_kind="history")
    assert codes(out) == ["block_invalid"]


@pytest.mark.parametrize("btype", [None, "", "Paragraph", "image", "heading", 3])
def test_unknown_block_types_are_refused(btype):
    out = V.validate_block("b", {"type": btype, "text": "Fine."}, section_kind="history")
    assert "block_type" in codes(out)


def test_unexpected_keys_are_refused_and_read_along_is_allowed():
    out = V.validate_block("b", {"type": "paragraph", "text": "Fine.", "attribution": "X"},
                           section_kind="history")
    assert "block_invalid" in codes(out)
    ok = {"type": "paragraph", "text": "Fine.",
          "readAlong": [{"text": "Fine.", "start": 0.0, "end": 1.0}]}
    assert V.validate_block("b", ok, section_kind="history") == []


@pytest.mark.parametrize("text", ["", "   ", "\u200b\u200b", "\n\t"])
def test_empty_text_is_refused(text):
    assert "empty" in para_codes(text)


def test_a_6000_char_paragraph_is_capped_fast():
    started = time.perf_counter()
    out = para("x" * 6000)
    assert "too_long" in codes(out)
    out = para(("Buy now and hold the shares. " * 400)[:6000])
    assert {"too_long", "word_limit"} <= set(codes(out))
    out = para("a" * 1_000_000)
    assert "too_long" in codes(out)
    assert time.perf_counter() - started < 3.0
    # The cap applies BEFORE the scan: a refusal past the cap is never read (and the block is
    # refused for its length anyway).
    late = "Plain words here. " * 80 + "Caydex"
    assert len(late) > V.BLOCK_TEXT_CAP and "forbidden_token" not in para_codes(late)
    assert "too_long" in para_codes(late)


def test_issue_is_frozen_and_serialisable():
    i = V.Issue("f", "c", "d")
    with pytest.raises(Exception):
        i.code = "x"   # type: ignore[misc]
    assert i.as_dict() == {"field": "f", "code": "c", "detail": "d"}


def test_issues_are_deduplicated_and_details_capped():
    out = para("Caydex " * 50)
    assert len(out) == len({(i.field, i.code, i.detail) for i in out})
    assert all(len(i.detail) <= 160 for i in out)


def test_validate_block_never_raises(monkeypatch, caplog):
    def boom(*_a, **_k):
        raise RuntimeError("scanner exploded")

    monkeypatch.setattr(compliance, "scan_text", boom)
    with caplog.at_level(logging.ERROR):
        out = para("A plain sentence.")
    assert codes(out) == ["validator_error"] and out[0].detail == "RuntimeError"
    assert "validate_block failed" in caplog.text


# ═════════════════════════════════════════════════════════════════════════════════════════════
# H. Grounding
# ═════════════════════════════════════════════════════════════════════════════════════════════

SHEET = C.FactSheet(
    kind=C.KIND_DEEP_DIVE, topic_title="AI data centres", theme_slug="ai-infrastructure",
    subjects=(C.Subject("CoreWeave, Inc.", "0001769628", "CRWV"),),
    facts=(
        C.Fact("S1", "CoreWeave reported revenue of $1.2 billion for the quarter ended June 30, "
                     "2025, in its Form 10-Q.", C.ORIGIN_SEC_XBRL, accession="0001769628-25-000010"),
        C.Fact("S2", "CoreWeave reported a net loss of $290 million for the quarter ended June 30, "
                     "2025.", C.ORIGIN_SEC_XBRL),
    ),
)


@pytest.fixture(scope="module")
def ctx():
    return V.build_grounding_context(SHEET)


def test_a_grounded_figure_passes(ctx):
    assert para("CoreWeave reported revenue of $1.2 billion for the quarter.",
                grounding_ctx=ctx) == []


@pytest.mark.parametrize("text, code", [
    ("CoreWeave reported revenue of $3.4 billion for the quarter.", "ungrounded_number"),
    ("CoreWeave reported a net profit of $290 million.", "number_context"),
    ("Oracle reported revenue of $1.2 billion.", "ungrounded_entity"),
])
def test_invented_figures_and_names_fail(ctx, text, code):
    assert code in para_codes(text, grounding_ctx=ctx), para(text, grounding_ctx=ctx)


def test_grounding_runs_per_bullet_item(ctx):
    block = {"type": "bulletList", "items": ["Revenue was $1.2 billion.", "Revenue was $9 billion."]}
    out = V.validate_block("b", block, section_kind="history", grounding_ctx=ctx)
    assert Issue_at(out, "b.items[1]", "ungrounded_number") and not any(
        i.field == "b.items[0]" for i in out), out


@pytest.mark.parametrize("bad", [
    None, "a sheet", {"facts": []}, 5,
    C.FactSheet(C.KIND_DEEP_DIVE, "t", None, (), ()),
    C.FactSheet(C.KIND_DEEP_DIVE, "t", None, (), ("a string", None, 5)),     # junk facts
    C.FactSheet(C.KIND_DEEP_DIVE, "t", None, (), 5),                          # not iterable
    C.FactSheet(C.KIND_DEEP_DIVE, "t", None, (), (C.Fact("S1", "   ", C.ORIGIN_SEC_XBRL),)),
])
def test_a_bad_or_empty_sheet_grounds_nothing(bad, caplog):
    with caplog.at_level(logging.ERROR):
        empty = V.build_grounding_context(bad)
    assert empty.numbers == ()
    assert "ungrounded_number" in para_codes("Revenue was $1.2 billion.", grounding_ctx=empty)
    assert "grounding" in caplog.text


def test_a_junk_fact_beside_a_real_one_keeps_the_real_one():
    sheet = C.FactSheet(C.KIND_DEEP_DIVE, "t", None, (),
                        (None, SHEET.facts[0], "junk", C.Fact("S9", "x" * 50_000, C.ORIGIN_SEC_PEG)))
    ctx = V.build_grounding_context(sheet)
    assert para("CoreWeave reported revenue of $1.2 billion for the quarter.",
                grounding_ctx=ctx) == []


def test_the_grounding_vocabulary_is_the_marketing_writers():
    """Same construction as `content_pool.load_corpus` (whose items carry `full_vocab(vocab)`)."""
    item = next(iter(content_pool.load_corpus().values()))
    assert grounding.full_vocab(V.corpus_vocab()) == item.grounding.vocab
    assert len(V.corpus_vocab()) > 1000


def test_an_unreadable_corpus_is_not_cached(monkeypatch, caplog):
    monkeypatch.setattr(V, "_VOCAB", None)
    monkeypatch.setattr(content_pool, "_raw_items", lambda: [])
    with caplog.at_level(logging.ERROR):
        assert V.corpus_vocab() == frozenset()
    assert "NO vocabulary" in caplog.text
    assert V._VOCAB is None, "an empty vocabulary must be retried, not cached"


# ═════════════════════════════════════════════════════════════════════════════════════════════
# I. Articles
# ═════════════════════════════════════════════════════════════════════════════════════════════

# Twenty-five neutral sentences; paragraph i is a cyclic window, so every paragraph is distinct.
POOL = [
    "A data center turns electricity into computing work for its customers.",
    "Each building holds long rows of servers, cooling systems and power equipment.",
    "Chips that train large models draw far more power than older servers did.",
    "Operators sign long contracts with utilities so the lights never flicker.",
    "Cooling matters because heat limits how densely the racks can be packed.",
    "Builders compete for land near transmission lines and fiber routes.",
    "A lease often runs for many years, which gives the owner steady revenue.",
    "Customers pay for capacity whether or not they use all of it.",
    "Debt funds much of the construction, so interest costs shape the margins.",
    "When demand slows, empty halls still cost money to maintain.",
    "Engineers measure efficiency by comparing total power with the power the servers use.",
    "Water use has become a local political question in dry regions.",
    "Some operators build their own substations to shorten the wait for grid access.",
    "Networking gear links thousands of chips so they can share a single job.",
    "Training a model is a burst of work, while inference is a steady stream.",
    "Inference demand grows with every person who asks a model a question.",
    "Hardware ages quickly, so owners plan for replacement cycles.",
    "A customer that signs early may secure a lower price per unit of power.",
    "Concentration is a risk when a few large tenants fill most of the space.",
    "Investors read the footnotes to see how long the contracts last.",
    "Backlog shows contracted work that has not yet turned into revenue.",
    "Capital spending rises before revenue arrives, which strains cash flow.",
    "History shows that building booms can overshoot real demand.",
    "Railroads and fiber networks both saw heavy building followed by painful gluts.",
    "The lesson is to compare the pace of spending with the pace of paying customers.",
]


def _paragraph(i):
    out, words = [], 0
    for k in range(len(POOL)):
        s = POOL[(i + k) % len(POOL)]
        if words + len(s.split()) > C.MAX_PARAGRAPH_WORDS:
            break
        out.append(s)
        words += len(s.split())
    return " ".join(out)


ORDINALS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "eleven", "twelve", "thirteen"]


def make_article(kinds=("peg", "history", "history", "mechanics", "mechanics", "lessons"),
                 paragraphs_per_section=4):
    sections, n = [], 0
    for s, kind in enumerate(kinds):
        content = []
        for _ in range(paragraphs_per_section):
            text = _paragraph(n)
            if kind == "peg" and not content:
                text = "This week, on October 21, 2026, the company disclosed a new contract."
            content.append({"type": "paragraph", "text": text})
            n += 1
        sections.append({"kind": kind, "title": f"Part {ORDINALS[s]} of the build-out",
                         "icon": "server.rack", "content": content})
    return {
        "slug": "the-ai-power-crunch-2026-10-23",
        "title": "The AI Power Crunch",
        "subtitle": "Why electricity became the scarce input of computing",
        "cardSubtitle": "How power shapes the economics of data centers",
        "tagLabel": "DEEP DIVE",
        "category": "blueprints",
        "author": dict(C.AUTHOR),
        "readTimeMinutes": 11,
        "viewCount": "",
        "heroGradientColors": ["0D9488", "0F766E", "134E4A"],
        "keyHighlights": [
            {"icon": "bolt.fill", "title": "Power first",
             "description": "Grid access sets the pace of building."},
            {"icon": "cpu.fill", "title": "Chips draw more",
             "description": "Training clusters need dense power."},
            {"icon": "chart.bar.fill", "title": "Contracts matter",
             "description": "Long leases steady the revenue."},
        ],
        "sections": sections,
    }


def article_issues(article, kind=C.KIND_DEEP_DIVE, **kw):
    kw.setdefault("served_titles", ["How Amazon Built Its Moat"])
    kw.setdefault("in_flight_titles", [])
    return V.validate_article(article, kind=kind, **kw)


def article_codes(article, **kw):
    return codes(article_issues(article, **kw))


def test_the_fixture_article_is_clean_and_in_bounds():
    article = make_article()
    out = article_issues(article)
    assert out == [], out
    words = sum(V.word_count(b["text"]) for s in article["sections"] for b in s["content"])
    assert C.DEEP_DIVE_WORDS[0] <= words <= C.DEEP_DIVE_WORDS[1], words


@pytest.mark.parametrize("key", V.REQUIRED_ARTICLE_KEYS)
def test_every_required_key_is_required(key):
    article = make_article()
    del article[key]
    out = article_issues(article)
    assert Issue_at(out, key, "missing_key"), out


@pytest.mark.parametrize("content", [None, [], "article", 5, True, ()])
def test_non_dict_articles_are_refused(content):
    assert article_codes(content) == ["article_invalid"]


@pytest.mark.parametrize("slug", [
    "The-AI-Power-Crunch-2026-10-23", "the ai power crunch 2026-10-23", "the-ai-power-crunch",
    "the-ai-power-crunch-2026-13-40", "the-ai-power-crunch-26-10-23", "", None, 5,
    "a" * 300 + "-2026-10-23", "the-ai-power-crunch-2026-10-23\n",
])
def test_bad_slugs_are_refused(slug):
    article = make_article()
    article["slug"] = slug
    assert {"slug_pattern"} & set(article_codes(article)) or "too_long" in article_codes(article)


def test_slug_collision():
    article = make_article()
    assert "slug_collision" in article_codes(article, served_slugs=[article["slug"]])
    assert "slug_collision" in article_codes(article, served_slugs=article["slug"])  # bare str
    assert "slug_collision" not in article_codes(article, served_slugs=None)
    assert "slug_collision" not in article_codes(article, served_slugs=5)            # junk


@pytest.mark.parametrize("category", ["Blueprints", "deepDive", "", None, 3])
def test_category_must_be_an_enum_value(category):
    article = make_article()
    article["category"] = category
    assert "bad_value" in article_codes(article)


@pytest.mark.parametrize("verified", [True, None, "false", 0, 1])
def test_author_is_never_verified(verified):
    article = make_article()
    article["author"] = {**C.AUTHOR, "isVerified": verified}
    assert "verified" in article_codes(article)
    del article["author"]["isVerified"]
    assert "verified" in article_codes(article)


def test_author_shape_and_disclosure():
    article = make_article()
    article["author"] = {**C.AUTHOR, "title": "Editorial"}
    assert "disclosure" in article_codes(article)
    for bad in (None, "Caydex", 5, []):
        article["author"] = bad
        assert "bad_value" in article_codes(article)
    article["author"] = {"isVerified": False}
    assert article_codes(article).count("bad_value") == 2


@pytest.mark.parametrize("minutes", [True, False, 0, -1, 5.0, float("nan"), math.inf, 10**9, "5",
                                     None])
def test_read_time_must_be_a_sane_whole_number(minutes):
    article = make_article()
    article["readTimeMinutes"] = minutes
    assert "bad_value" in article_codes(article)


@pytest.mark.parametrize("key, value", [
    ("viewCount", 0), ("viewCount", "1.2K"), ("viewCount", None), ("viewCount", False),
    ("viewCount", 12_000), ("learnerCount", 340), ("learnerCount", "2K"), ("commentCount", 7),
    ("likeCount", True),
])
def test_engagement_counts_are_never_fabricated(key, value):
    article = make_article()
    article[key] = value
    assert "engagement_count" in article_codes(article)


def test_empty_engagement_strings_pass():
    article = make_article()
    article.update(learnerCount="", commentCount="")
    assert article_issues(article) == []


@pytest.mark.parametrize("colors", [
    ["0D9488", "0F766E"], ["#0D9488", "0F766E", "134E4A"], [1, 2, 3], ["GGGGGG", "0F766E", "134E4A"],
    "0D9488", None, ["0D9488", "0F766E", "134E4A", "000000"],
])
def test_gradient_needs_three_bare_hex_colours(colors):
    article = make_article()
    article["heroGradientColors"] = colors
    assert "bad_value" in article_codes(article)


def test_key_highlights_shape_icons_and_duplicates():
    article = make_article()
    article["keyHighlights"] = article["keyHighlights"][:2]
    assert "bad_value" in article_codes(article)
    article = make_article()
    article["keyHighlights"][0]["icon"] = "sparkles.2"
    assert Issue_at(article_issues(article), "keyHighlights[0].icon", "icon")
    article = make_article()
    article["keyHighlights"][1]["title"] = "POWER FIRST"
    assert Issue_at(article_issues(article), "keyHighlights[1].title", "duplicate")
    article = make_article()
    article["keyHighlights"][2] = "a string"
    assert "bad_value" in article_codes(article)
    article = make_article()
    article["keyHighlights"][0]["description"] = "Buy low and hold the shares."
    assert Issue_at(article_issues(article), "keyHighlights[0].description", "class_b_recommendation")


@pytest.mark.parametrize("sections", [[], None, "x", 5, {}])
def test_sections_must_be_a_non_empty_list(sections):
    article = make_article()
    article["sections"] = sections
    assert "bad_value" in article_codes(article)


def test_draft_sections_need_a_kind():
    article = make_article()
    del article["sections"][2]["kind"]
    assert Issue_at(article_issues(article), "sections[2].kind", "section_kind")
    article["sections"][2]["kind"] = "intro"
    assert Issue_at(article_issues(article), "sections[2].kind", "section_kind")


def test_section_shape_problems():
    article = make_article()
    article["sections"][1]["content"] = []
    assert Issue_at(article_issues(article), "sections[1].content", "bad_value")
    article = make_article()
    article["sections"][1] = "not a section"
    assert Issue_at(article_issues(article), "sections[1]", "bad_value")
    article = make_article()
    article["sections"][1]["icon"] = "sparkles.2"
    assert Issue_at(article_issues(article), "sections[1].icon", "icon")
    article = make_article()
    article["sections"][3]["title"] = article["sections"][2]["title"].upper()
    assert Issue_at(article_issues(article), "sections[3].title", "duplicate")


def test_a_repeated_paragraph_is_a_duplicate():
    article = make_article()
    article["sections"][4]["content"][1] = dict(article["sections"][2]["content"][0])
    assert Issue_at(article_issues(article), "sections[4].content[1]", "duplicate")


def test_huge_structures_are_bounded():
    article = make_article()
    article["sections"] = article["sections"] * 2000
    started = time.perf_counter()
    out = article_issues(article)
    assert "bad_value" in codes(out)
    assert time.perf_counter() - started < 20.0
    article = make_article()
    article["sections"][0]["content"] = article["sections"][0]["content"] * 50
    assert Issue_at(article_issues(article), "sections[0].content", "bad_value")


def test_word_count_bounds_per_kind():
    deep = make_article()
    assert "word_count" in article_codes(deep, kind=C.KIND_SLOT)          # ~1,680 > 1,250
    short = make_article(kinds=("peg", "history", "lessons"), paragraphs_per_section=4)
    assert "word_count" in article_codes(short)                            # < 1,400
    slot = make_article(kinds=("peg", "history", "history", "mechanics", "lessons"),
                        paragraphs_per_section=3)                          # ~1,000 words
    assert "word_count" not in article_codes(slot, kind=C.KIND_SLOT)
    assert "word_count" in article_codes(slot, kind=C.KIND_DEEP_DIVE)
    # An unknown kind is reported, and no word bounds are guessed for it.
    assert "kind" in article_codes(deep, kind="podcast")
    assert "word_count" not in article_codes(deep, kind="podcast")


def test_peg_share_is_capped():
    article = make_article(kinds=("peg", "peg", "history", "mechanics", "mechanics", "lessons"))
    out = article_issues(article)
    assert "peg_share" in codes(out), out
    assert "peg_share" not in article_codes(make_article())


def test_title_collisions_are_case_and_accent_folded():
    article = make_article()
    for served in (["  the AI POWER crunch "], ["The AI Power Crunch."], ["The \u00c1I Power Crunch"],
                   "THE AI POWER CRUNCH"):
        assert "title_collision" in article_codes(article, served_titles=served), served
    assert "title_collision" in article_codes(article, served_titles=[],
                                              in_flight_titles=["the ai power crunch"])
    for harmless in (None, [], [None, 5, ""], 5, ["The AI Power Crunch, Part Two"]):
        assert "title_collision" not in article_codes(article, served_titles=harmless)


def test_header_fields_never_carry_time_words_or_advice():
    article = make_article()
    article["title"] = "Why AI Power Matters Today"
    article["subtitle"] = "You should buy the builders"
    out = article_issues(article)
    assert Issue_at(out, "title", "time_word") and Issue_at(out, "subtitle", "advice_directive")


def test_statistics_are_text_scanned_but_not_grounded():
    article = make_article()
    article["statistics"] = [{"label": "Revenue", "value": "$1.2B"}]
    assert article_issues(article, sheet=None) == []
    article["statistics"] = [{"label": "Caydex pick", "value": "Buy now"}]
    out = article_issues(article)
    assert Issue_at(out, "statistics[0].label", "forbidden_token")
    article["statistics"] = "x"
    assert "bad_value" in article_codes(article)


def test_article_grounding_against_the_sheet(ctx):
    article = make_article()
    article["sections"][1]["content"][0] = {
        "type": "paragraph", "text": "CoreWeave reported revenue of $9.9 billion for the quarter."}
    out = article_issues(article, sheet=SHEET)
    assert Issue_at(out, "sections[1].content[0]", "ungrounded_number"), out
    # The sheet's subjects reach the conduct rule.
    article["sections"][1]["content"][0] = {"type": "paragraph",
                                            "text": "CoreWeave misled its customers."}
    assert Issue_at(article_issues(article, sheet=SHEET), "sections[1].content[0]", "conduct_claim")
    # An ALL-CAPS label is grounded in title case: its ordinary words pass, a ticker does not.
    out = article_issues(article, sheet=SHEET)
    assert not any(i.field == "tagLabel" for i in out), out
    article["tagLabel"] = "NVDA DEEP DIVE"
    assert Issue_at(article_issues(article, sheet=SHEET), "tagLabel", "ungrounded_entity")


def test_a_bad_sheet_is_reported_and_fails_closed():
    article = make_article()
    out = article_issues(article, sheet={"facts": []})
    assert "sheet_invalid" in codes(out)


def test_validate_article_never_raises(monkeypatch, caplog):
    def boom(*_a, **_k):
        raise ValueError("bad")

    monkeypatch.setattr(V, "_title_keys", boom)
    with caplog.at_level(logging.ERROR):
        out = article_issues(make_article())
    assert codes(out) == ["validator_error"]
    assert "validate_article failed" in caplog.text and "the-ai-power-crunch-2026-10-23" in caplog.text


# ═════════════════════════════════════════════════════════════════════════════════════════════
# J. Review 2026-10-23 (hard_safety F1/F2/F4/F5): trade verbs, the reader's holdings, valuation
#    verdicts, attributed quotations, authorship lines, complaint wording
# ═════════════════════════════════════════════════════════════════════════════════════════════

CRWV_SUBJECTS = ["CoreWeave, Inc.", "CRWV"]
MONEY_MOVES_DATA = BACKEND / "data" / "money_moves.json"


@pytest.fixture(scope="module")
def bundle_texts():
    doc = json.loads(MONEY_MOVES_DATA.read_text(encoding="utf-8"))
    return [t for a in doc["articles"] for sec in a["sections"] for b in sec["content"]
            for t in ([b.get("text")] + list(b.get("items") or [])) if isinstance(t, str)]


def bundle_sentence(texts, fragment):
    """The bundle sentence holding `fragment`: every new row's must-pass twin is a real line of
    the 23 hand-made articles (the corpus that tells over-blocking from a catch)."""
    for t in texts:
        for sent in compliance.sentences(compliance.clean(t)):
            if fragment in sent:
                return sent
    raise AssertionError(f"twin {fragment!r} is no longer in the bundle")


def bundle_block(texts, fragment):
    for t in texts:
        if fragment in t:
            return t
    raise AssertionError(f"twin {fragment!r} is no longer in the bundle")


@pytest.mark.parametrize("text, detail", [
    ("Buy CoreWeave.", "buy … coreweave"),
    ("Sell CoreWeave now.", "sell … coreweave"),
    ("Short CoreWeave stock.", "short … coreweave"),
    ("Load up on CoreWeave before the next filing.", "load up on … coreweave"),
    ("Consider trimming your CoreWeave position.", "consider trimming … coreweave"),
    ("It may be wise to sell your CoreWeave shares.", "sell … coreweave"),
    ("Investors buy CoreWeave shares.", "buy … coreweave"),   # a named STOCK traded, any subject
    ("Sell the shares now.", "sell … shares"),
    ("Avoid the stock until the backlog clears.", "avoid … stock"),
    ("BUY CRWV.", "buy … crwv"),
    ("Buy Core\u200bWeave.", "buy … coreweave"),               # zero-width inside the name
])
def test_a_trade_verb_on_a_subject_or_a_security_is_a_directive(text, detail):
    out = para(text, subjects=CRWV_SUBJECTS)
    assert any(i.code == "advice_directive" and i.detail == detail for i in out), out


@pytest.mark.parametrize("text", [
    "Companies sell shares to raise money.",                 # a finite verb with its own subject
    "The company used cash to buy back shares.",             # a buyback, not a reader's trade
    "Customers buy CoreWeave capacity by the hour.",         # the subject's product, not its stock
    "CoreWeave sold new shares to the public in its listing.",
    "The issuer chose to sell new shares to fund the build-out.",
    "Investors who hold a short position pay to borrow shares.",
    "In the short run, demand for capacity can swing.",
    "Short-term contracts give customers room to leave.",
    "Buy-and-hold investors read the annual report.",
])
def test_trade_words_in_business_english_pass(text):
    assert "advice_directive" not in para_codes(text, subjects=CRWV_SUBJECTS), \
        para(text, subjects=CRWV_SUBJECTS)


@pytest.mark.parametrize("fragment, subjects", [
    ("would never sell a chip of its own", ()),
    ("Both companies sell commodity goods", ()),
    ("Most acquirers buy a company", ()),
    ("broader technology sell-off", ()),
    ("sell as cheaply as it possibly can", ("Costco Wholesale Corporation", "COST")),
])
def test_the_trade_row_twins_from_the_bundle_pass(bundle_texts, fragment, subjects):
    sent = bundle_sentence(bundle_texts, fragment)
    assert para(sent, subjects=subjects) == [], para(sent, subjects=subjects)


@pytest.mark.parametrize("text, detail", [
    ("Consider your CoreWeave position.", "your coreweave position"),
    ("Rebalance your portfolio every year.", "your portfolio"),
    ("Check your stock holdings before the filing.", "your stock holdings"),
    ("Trim your stake.", "your stake"),
])
def test_the_readers_own_holdings_are_a_directive(text, detail):
    out = para(text, subjects=CRWV_SUBJECTS)
    assert any(i.code == "advice_directive" and i.detail == detail for i in out), out


def test_the_holdings_row_twins_pass(bundle_texts):
    assert para(bundle_sentence(bundle_texts, "your photo library")) == []
    assert "advice_directive" not in para_codes("Your share of the profits grows with the business.")


@pytest.mark.parametrize("text, subjects, detail", [
    ("CoreWeave stock is undervalued and looks like a bargain right now.", CRWV_SUBJECTS,
     "undervalued"),
    ("CoreWeave is overvalued and the bubble is about to burst.", CRWV_SUBJECTS, "overvalued"),
    ("CoreWeave stock is a must-own.", CRWV_SUBJECTS, "must-own"),
    ("CoreWeave stock looks cheap.", CRWV_SUBJECTS, "stock looks cheap"),     # bound by the row
    ("CoreWeave is a bargain stock.", CRWV_SUBJECTS, "bargain … stock"),     # about the stock
    ("NVIDIA stock looks like a bargain.", (), "stock looks like a bargain"),   # lexicon company
    ("Shares of Costco are expensive.", (), "costco are expensive"),            # bound by the row
    ("Microsoft looks over-valued.", (), "over-valued"),
])
def test_a_valuation_verdict_about_a_named_company_fails(text, subjects, detail):
    out = para(text, subjects=subjects)
    assert any(i.code == "valuation_verdict" and i.detail == detail for i in out), out


@pytest.mark.parametrize("fragment", [
    "Microsoft could afford a long, expensive transition",   # class_b_evaluative fires here
    "cheaper technology",
    "cheaper ad-supported tiers",
    "moving away is expensive and risky",
])
def test_the_valuation_twins_from_the_bundle_pass(bundle_texts, fragment):
    sent = bundle_sentence(bundle_texts, fragment)
    assert para(sent, subjects=("Microsoft Corporation", "MSFT")) == [], para(sent)


@pytest.mark.parametrize("text", [
    "A value trap looks cheap on paper while the business shrinks.",   # no company named
    "The stock looked cheap.",                                         # no company named
    "Costco sells cheap hot dogs.",                                    # cheap goods, not stock
    "The market cap passed one trillion dollars in 2024.",
])
def test_valuation_vocabulary_without_a_verdict_on_a_company_passes(text):
    assert "valuation_verdict" not in para_codes(text, subjects=CRWV_SUBJECTS), para(text)


@pytest.mark.parametrize("text", [
    'The chief executive told investors the company "has never been stronger."',
    "The chief executive called the deal \u201ctransformational\u201d for the business.",
    '"We delivered a record quarter," said the chief executive.',
    'According to CoreWeave, "demand is unprecedented".',
    'Analysts described it as "a game changer" for the industry.',
    'In its words, "capacity is the product".',
    'The CEO (who said "we are strong") left the company.',      # not a defined term
    'The chief executive said "growth',                          # an unclosed quote
    '"Capacity is the product," the founders wrote.',
    '"Capacity is the product." CoreWeave',                      # a subject right after
])
def test_an_attributed_quotation_fails(text):
    assert "quotation" in para_codes(text, subjects=CRWV_SUBJECTS), para(text, subjects=CRWV_SUBJECTS)


@pytest.mark.parametrize("fragment", [
    "could someone build this?",
    "whose posts should I show?",
    "the challenger won",
    "do not make big bets",
])
def test_the_four_framing_quotes_in_the_bundle_pass(bundle_texts, fragment):
    block = bundle_block(bundle_texts, fragment)
    assert "quotation" not in para_codes(block), para(block)


def test_a_filings_defined_term_is_not_a_quotation():
    text = 'CoreWeave, Inc. (the "Company") entered into an agreement (the "Agreement") with a customer.'
    assert "quotation" not in para_codes(text, subjects=CRWV_SUBJECTS)
    text = 'The notes (collectively, the "Notes" or "2031 Notes") mature in one tranche.'
    assert "quotation" not in para_codes(text)


@pytest.fixture(scope="module")
def gemini_ctx():
    sheet = C.FactSheet(
        kind=C.KIND_DEEP_DIVE, topic_title="AI models", theme_slug="ai",
        subjects=(C.Subject("Alphabet Inc.", "0001652044", "GOOGL"),),
        facts=(C.Fact("S1", "Google Cloud, which sells access to the Gemini models, reported revenue "
                            "of $15.2 billion for the quarter ended June 30, 2026.", C.ORIGIN_SEC_XBRL),))
    return V.build_grounding_context(sheet)


@pytest.mark.parametrize("text", [
    "This article was drafted with the Gemini models from Google Cloud.",
    "Articles in this series are written by Gemini.",
    "The narration was produced using Google models.",
])
def test_the_model_vendor_as_the_articles_author_fails(gemini_ctx, text):
    out = V.validate_block("b", {"type": "paragraph", "text": text}, section_kind="mechanics",
                           grounding_ctx=gemini_ctx, subjects=["Alphabet Inc.", "GOOGL"])
    assert "authorship_reveal" in codes(out), out
    # Grounded on the sheet, nothing else refuses it: this row is the only gate.
    assert codes(out) == ["authorship_reveal"], out


def test_an_authorship_line_with_no_vendor_or_no_article_passes(bundle_texts, gemini_ctx):
    for text in ("Google Cloud sells access to the Gemini models.",
                 "Frontier models trained by OpenAI run on these clusters.",
                 "The analysis of the filing was produced by the company's auditors."):
        assert "authorship_reveal" not in para_codes(text), para(text)
    sent = bundle_sentence(bundle_texts, "Gemini assistant across Gmail")
    assert para(sent) == [], para(sent)
    assert "authorship_reveal" in para_codes(
        "You are reading a summary generated by a large language model.")


@pytest.mark.parametrize("text", [
    "The lawsuit alleges the supplier delayed shipments.",
    "A complaint says the firm overstated sales.",
    "The regulator is alleging a breach of the rules.",
    "The complaints claim the product failed.",
])
def test_complaint_wording_is_rumour(text):
    assert "rumour" in para_codes(text), para(text)


@pytest.mark.parametrize("text", [
    "Customer complaints fell after the redesign.",
    "Investors weighed the claims in the filing.",
])
def test_complaints_and_claims_as_plain_nouns_pass(text):
    assert "rumour" not in para_codes(text), para(text)


def test_the_complaint_wording_twin_from_the_bundle_passes(bundle_texts):
    sent = bundle_sentence(bundle_texts, "extraordinary claims are settled")
    assert "rumour" not in para_codes(sent), para(sent)


@pytest.mark.parametrize("text, subjects", [
    ("CoreWeave made materially false and misleading statements to investors.", CRWV_SUBJECTS),
    ("The company made false statements in its filings.", ()),
    ("Nvidia issued untrue statements about supply.", ()),
])
def test_false_statements_by_a_company_are_conduct(text, subjects):
    assert "conduct_claim" in para_codes(text, subjects=subjects), para(text, subjects=subjects)


def test_misleading_without_a_statement_or_a_company_passes(bundle_texts):
    assert "conduct_claim" not in para_codes("Misleading statements in old adverts became a "
                                             "lesson for marketers.")
    sent = bundle_sentence(bundle_texts, "that symmetry was misleading")
    assert "conduct_claim" not in para_codes(sent), para(sent)


def test_the_restated_unsafe_peg_lines_are_refused_with_the_sheet(ctx):
    """hard_safety F1/F3: once a span is on the sheet, grounding treats its restatement as
    sourced — these refusals must come from rows grounding cannot license."""
    for text, code in (
            ("CoreWeave made materially false and misleading statements to investors.",
             "conduct_claim"),
            ("CoreWeave shares are undervalued.", "valuation_verdict"),
            ("Buy CoreWeave.", "advice_directive")):
        assert code in para_codes(text, kind="mechanics", grounding_ctx=ctx,
                                  subjects=CRWV_SUBJECTS), text


def test_the_article_passes_its_sheet_subjects_to_the_new_rows():
    article = make_article()
    article["sections"][3]["content"][0]["text"] = "Buy CoreWeave."
    article["title"] = "CoreWeave Is Undervalued"
    out = article_issues(article, sheet=SHEET)
    assert Issue_at(out, "sections[3].content[0]", "advice_directive"), out
    assert Issue_at(out, "title", "valuation_verdict"), out


@pytest.mark.parametrize("junk", ['"' * 500, "\u201c" * 300 + "said", '(the "' * 200,
                                  "buy " * 400, "your " * 300])
def test_the_new_rows_stay_fast_and_total_on_pathological_text(junk):
    started = time.perf_counter()
    out = para(junk, subjects=CRWV_SUBJECTS)
    assert time.perf_counter() - started < 2.0
    assert "validator_error" not in codes(out), out


# ── share prices in ANY article block (2026-10-23: "$79.88 per share" reached a live draft) ────


@pytest.mark.parametrize("text", [
    "The premium was set over the last reported sale price of $79.88 per share of its stock.",
    "CoreWeave stock traded at $120 in September.",
    "The shares traded as high as $187 that year.",
    "The company's stock price rose sharply after the report.",
    "The offering was set at a price of $40.00 per share.",
    "The last reported sale price was $79.88.",
    "The market price of the common stock fell 18% that week.",
    "Its closing price on the day was a record.",
])
def test_a_share_price_is_refused_in_any_block(text):
    assert "share_price" in para_codes(text), para(text)


@pytest.mark.parametrize("text", [
    "Diluted earnings per share were $2.10 for the fiscal year ended January 25, 2026.",
    "The board declared a dividend of $0.25 per share.",
    "The buyer agreed to a purchase price of $95.00 per share in cash.",
    "Wholesale power trades at market prices that change every hour.",
    "The average selling price of its chips rose.",
    # the hand-written catalog's ideas, not prices (the corpus pins these articles)
    "Judging the battle on a single number — market share, units, or stock price — misses the point.",
    "Obvious durability gets capitalised into a share price long before it shows up in the earnings.",
    "Neither the crash nor the comeback arrives on the timetable a share price implies.",
    "It was the market price of the common stock that set the ratio.",
    "The conversion premium was measured against the sale price on September 17, 2026.",
])
def test_filed_per_share_facts_and_power_prices_are_not_share_prices(text):
    assert "share_price" not in para_codes(text), para(text)


def test_the_peg_screen_and_the_blocks_share_one_price_rule():
    from app.services.learn_weekly import topic
    assert topic._SHARE_PRICE_RE is V.SHARE_PRICE_RE
