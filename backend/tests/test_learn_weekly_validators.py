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
        "cashtag", "code_owned", "brand_mention", "person_named", "source_reference",
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


def test_author_shape_and_code_owned_author():
    article = make_article()
    assert "author_mismatch" not in article_codes(article)
    # The pre-2026-11-01 byline, a renamed author, an extra key: all refused.
    for other in ({**C.AUTHOR, "title": "AI-assisted · Editor-reviewed"},
                  {**C.AUTHOR, "name": "Cay AI"}, {**C.AUTHOR, "role": "writer"}):
        article["author"] = other
        assert "author_mismatch" in article_codes(article)
    # A stale byline is named even when isVerified is wrong too (not hidden behind it).
    article["author"] = {**C.AUTHOR, "title": "AI-assisted · Editor-reviewed", "isVerified": True}
    assert {"author_mismatch", "verified"} <= set(article_codes(article))
    article["author"] = {**C.AUTHOR, "isVerified": True}
    assert "author_mismatch" not in article_codes(article) and "verified" in article_codes(article)
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


# ── a company name with a digit is a name, not a quantity (2026-10-23 live dry run: "3M") ──────

_MMM_SHEET = C.FactSheet(C.KIND_DEEP_DIVE, "3M CO", None, (C.Subject("3M CO", "0000066740", "MMM"),), (
    C.Fact("S1", "3M CO reported revenue of $24.6 billion for the fiscal year ended December 31, "
                 "2025 (Form 10-K).", C.ORIGIN_SEC_XBRL, "https://www.sec.gov/x", "0000066740-26-000010"),))
_MMM = ["3M CO", "MMM"]


_NUMBER_CODES = {"number_context", "ungrounded_number", "ungrounded_name_number",
                 "subject_name_number"}


@pytest.mark.parametrize("text", [
    "How 3M Built a Diversified Industrial Business.",
    "Inside 3M's business model, one division at a time.",
    "3M reported revenue of $24.6 billion for the fiscal year ended December 31, 2025.",
    "In 2025, 3M reported revenue of $24.6 billion.",                # a clause after a comma
    "3M products reach many industries.",                            # the sentence's subject
    "Products from 3M reach many industries.",
    "The relationship between 3M and the retailer expanded.",
    "3M CO operates in many markets.",
    "3M\u2019s optical connectivity products are sold to data centers.",
    "Over 3M's long history, the company kept investing.",           # a possessive wins
    # Review round 2 (2026-10-23): a name-position allow-list refused these, and the live outline
    # died on "Origins of 3M". The name is the company everywhere but in an amount shape.
    "Origins of 3M", "The Origins of 3M", "The 3M Story", "The Rise of 3M", "Understanding 3M",
    "What 3M Teaches About Diversification", "Is 3M Still a Conglomerate?", "Meet 3M",
    "3M In Focus", "The company sells 3M products.", "Research spending is central to the 3M model.",
    "Many 3M products started as experiments in its labs.", "Each 3M segment faces its own competitors.",
    "Critics argue that conglomerates such as 3M can be slow to adapt.", "But 3M kept investing.",
    "As 3M grew, it diversified.", "Second, the 3M example shows how segments interact.",
    "3M-branded tape and filters", "The 3M-Amazon relationship", "3M in data centers",
    "Some 3M products reach households.", "Another 3M product line grew.",
    "Honeywell is larger than 3M.", "Only 3M offers both.", "The downturn hit 3M hard.",
    # Review round 3: words that also take a company as their object are not amount words.
    "3M took a year to integrate the unit.", "Customers buy from 3M each year.",
    "3M reports every quarter.", "That record makes 3M worth studying.",
    "A book about 3M explains its labs.", "Several brands sit under 3M.", "The task fell to 3M.",
    "Post-it Notes earned 3M a reputation for invention.", "The decision was up to 3M.",
    "Customers choose Amazon over 3M for some services.", "3M'S HISTORY",
    "Amazon competes with 3M in consumer products.", "Amazon partnered with 3M in data center sales.",
    "3M in its annual report described four segments.",
    # Review round 4.
    "Hospitals used 3M respirators every day during the pandemic.",
    "Health care workers wore 3M masks every day.",
    "Every dollar 3M invests in research is a bet on future products.",
    "Each dollar 3M returns to shareholders is a dollar it does not reinvest.",
    "The product line was dropped by 3M.", "Prices were increased by 3M.",
    "Its later brands grew from 3M research.", "That record makes 3M worth a closer look.",
])
def test_the_subjects_own_digit_name_is_not_a_number(text):
    ctx = V.build_grounding_context(_MMM_SHEET)
    assert not _NUMBER_CODES & set(para_codes(text, grounding_ctx=ctx, subjects=_MMM)), \
        para(text, grounding_ctx=ctx, subjects=_MMM)


@pytest.mark.parametrize("text", [
    "The deal was worth $3M in cash.",
    "3M held 3M in cash.",            # passed with NO violation while the sheet's name grounded 3M
    "3M paid 3M in fines.",
    "3M sold 3M worth of tape.",
    "3M sold 3M-dollar contracts.",
    "3M paid USD 3M to settle.",
    "3M sold nearly 3M units.",
    "3M made 3M masks a day.",
    "A 3m-wide roll of tape.",         # lower case is a length, never the name
    "Products from 3m reach many industries.",   # ... even where the name could stand
    "It paid $3M's worth of fees.",    # a currency before it beats the possessive after it
    # An amount where the name could also stand (a sentence start, a clause, "with"):
    "3M worth of tape was sold.",
    "3M paid 3M in back taxes.",
    "Paid with 3M in cash, the deal closed.",
    "The plant made, at most 3M rolls.",
    # Review round 2: change verbs and valuations before it.
    "Sales grew by 3M last year.", "The unit was valued at 3M.", "The stake was worth 3M.",
    "Revenue rose to 3M.", "Costs declined from 3M.", "The fund held more than 3M shares.",
    # Review round 3: "total" (its pattern could never match single spacing), more money nouns,
    # and words between "in" and the noun.
    "The fines came to a total of 3M.", "Sales totaling 3M were booked.",
    "Sales totalling 3M were booked.", "3M paid 3M in fees.", "3M booked 3M in annual revenue.",
    "3M took on 3M in new debt.", "3M paid 3M in taxes.",
    # Review round 4: a hedge in between, other tenses, compound money nouns, "'s worth of", and
    # a non-ASCII hyphen.
    "The stake was worth about 3M.", "Sales grew by about 3M.", "The unit was valued at around 3M.",
    "The plant cost an estimated 3M.", "The company spends 3M on research.",
    "Sales have grown by 3M.", "Revenue rises to 3M.", "Costs increase by 3M.",
    "Sales fell by 3M last year.", "3M bought back 3M in share repurchases.",
    "3M paid 3M in dividend payments.", "3M booked 3M in impairment charges.",
    "3M sold 3M's worth of tape.", "3M sold 3M\u2010dollar contracts.",
])
def test_a_digit_name_in_any_other_position_is_refused_with_its_own_code(text):
    """Review rounds (2026-10-23): a count-noun denylist let "3M in fines", "USD 3M" and "3M
    masks" through. Every amount shape is refused now, with a code that names the fix. Known
    residual: a bare count ("3M served 3M customers", "3M of product") reads as the name — the
    prompt forbids K/M/B abbreviations and the owner reviews every piece."""
    ctx = V.build_grounding_context(_MMM_SHEET)
    assert "subject_name_number" in para_codes(text, grounding_ctx=ctx, subjects=_MMM)


def test_the_sheets_own_name_grounds_no_number():
    """"3M CO reported revenue …" made 3,000,000 a sheet number, so an invented "3M" amount next
    to "reported" or "revenue" was grounded before any exemption ran."""
    ctx = V.build_grounding_context(_MMM_SHEET)
    assert 3_000_000 not in {n.value for n in ctx.numbers}
    assert 24_600_000_000 in {n.value for n in ctx.numbers}
    plain = C.FactSheet(C.KIND_DEEP_DIVE, "x", None, (C.Subject("NVIDIA CORP", "0001045810", "NVDA"),),
                        (C.Fact("S1", "NVIDIA CORP shipped 3M units in fiscal 2025 (Form 10-K).",
                                C.ORIGIN_SEC_XBRL, "https://www.sec.gov/x", "0001045810-25-000023"),))
    assert 3_000_000 in {n.value for n in V.build_grounding_context(plain).numbers}


@pytest.mark.parametrize("name,ticker,text", [
    ("1 800 FLOWERS COM INC", "FLWS", "The company opened 800 stores."),       # a bare numeral
    ("10x Genomics, Inc.", "TXG", "Sales rose 10x in five years."),              # a multiple
    ("10x Genomics, Inc.", "TXG", "10x Genomics reported revenue."),             # fail closed
])
def test_a_bare_numeral_or_a_multiple_in_a_name_is_never_exempt(name, ticker, text):
    sheet = C.FactSheet(C.KIND_DEEP_DIVE, name, None, (C.Subject(name, "0000000001", ticker),), (
        C.Fact("S1", f"{name} reported revenue of $1.2 billion for the fiscal year ended December 31, "
                     "2025 (Form 10-K).", C.ORIGIN_SEC_XBRL, "https://www.sec.gov/x",
               "0000000001-26-000010"),))
    ctx = V.build_grounding_context(sheet)
    codes = set(para_codes(text, grounding_ctx=ctx, subjects=[name, ticker]))
    assert codes & _NUMBER_CODES and "subject_name_number" not in codes
    # The sheet's own name grounds none of its digits either ("10x" anchored to "revenue").
    values = {n.value for n in ctx.numbers}
    assert 1_200_000_000.0 in values and values <= {1_200_000_000.0, 31.0, 2025.0}, values


def test_a_figure_that_equals_a_name_digit_stays_a_sheet_figure():
    """Only the name AS WRITTEN is masked: "800 stores" filed by 1-800-Flowers is still a figure."""
    name = "1 800 FLOWERS COM INC"
    sheet = C.FactSheet(C.KIND_DEEP_DIVE, name, None, (C.Subject(name, "0000000001", "FLWS"),), (
        C.Fact("S1", f"{name} operated 800 stores at the fiscal year ended June 29, 2025 "
                     "(Form 10-K).", C.ORIGIN_SEC_XBRL, "https://www.sec.gov/x",
               "0000000001-25-000010"),))
    assert 800 in {n.value for n in V.build_grounding_context(sheet).numbers}


def test_the_exemption_is_only_for_the_pieces_own_subject():
    """On another company's piece, "3M" is not a subject token: refused as before."""
    ctx = V.build_grounding_context(_MMM_SHEET)
    text = "How 3M Built a Diversified Industrial Business."
    codes = set(para_codes(text, grounding_ctx=ctx, subjects=["General Motors Co", "GM"]))
    assert codes & {"number_context", "ungrounded_number"} and "subject_name_number" not in codes


def test_the_naming_refusal_has_a_repair_hint():
    from app.services.learn_weekly import prompts as P
    hint = P.hint("subject_name_number")
    assert "amount" in hint and "fact sheet" in hint
    assert "3M" in P.SYSTEM_BODY and "K, M or B" in P.SYSTEM_BODY


@pytest.mark.parametrize("name,fact", [
    ("1 800 FLOWERS COM INC", "1-800-Flowers.com reported revenue of $1.2 billion in fiscal 2025."),
    ("10X GENOMICS, INC.", "10x Genomics reported revenue of $1.2 billion in fiscal 2025."),
])
def test_a_digit_name_spelled_another_way_on_the_sheet_grounds_no_number(name, fact):
    """Review round 2: only the exact SEC name was masked, so a library or peg fact spelling it
    another way still grounded "800" or "10x" next to "revenue"."""
    sheet = C.FactSheet(C.KIND_DEEP_DIVE, name, None, (C.Subject(name, "0000000001", "X"),), (
        C.Fact("L:x:f1", fact, C.ORIGIN_SEC_XBRL, "https://www.sec.gov/x", "0000000001-26-000010"),))
    values = {n.value for n in V.build_grounding_context(sheet).numbers}
    assert 1_200_000_000.0 in values and not values & {1.0, 10.0, 800.0}, values


def test_a_token_the_text_does_not_show_reads_as_an_amount():
    """Fail closed: a violation naming a token the text does not show is never exempted."""
    assert V._reads_as_an_amount("3m", "No such token here.") is True
    assert V._reads_as_an_amount("3m", "3M reported revenue.") is False


def test_the_sheets_own_name_grounds_no_magnitude():
    """Review round 3: masking covered the sheet's numbers, not its MAGNITUDES, so "3M CO
    reported …" still grounded "millions" on a sheet that states only billions."""
    facts = tuple(C.Fact(f"S{i}", t, C.ORIGIN_SEC_XBRL, "https://www.sec.gov/x", "0000066740-26-000010")
                  for i, t in enumerate((
                      "3M CO reported revenue of $24.6 billion for the fiscal year ended December 31, "
                      "2025 (Form 10-K).",
                      "3M CO reported research and development expense of $1.1 billion for the fiscal "
                      "year ended December 31, 2025 (Form 10-K)."), start=1))
    sheet = C.FactSheet(C.KIND_DEEP_DIVE, "3M CO", None, (C.Subject("3M CO", "0000066740", "MMM"),),
                        facts)
    ctx = V.build_grounding_context(sheet)
    assert {scale for scale, _ in ctx.magnitudes} == {"billion"}
    for text in ("3M spends millions on research each year.",
                 "The company pays millions in dividends every fiscal year."):
        assert "ungrounded_number" in para_codes(text, grounding_ctx=ctx, subjects=_MMM)
    assert para_codes("3M reported revenue of $24.6 billion.", grounding_ctx=ctx, subjects=_MMM) == []


def test_a_real_dollar_figure_on_the_sheet_is_never_masked():
    """Review round 3: the loose name pattern had no currency guard, so "$3M" on a 3M sheet was
    masked away and the writer's correct restatement refused."""
    sheet = C.FactSheet(C.KIND_DEEP_DIVE, "3M CO", None, (C.Subject("3M CO", "0000066740", "MMM"),), (
        C.Fact("P1", "3M paid a $3M fee under the agreement.", C.ORIGIN_SEC_PEG,
               "https://www.sec.gov/x", "0000066740-26-000308"),))
    assert 3_000_000 in {n.value for n in V.build_grounding_context(sheet).numbers}


def test_an_upper_case_possessive_is_the_name():
    """A tagLabel is upper case: "3M'S" must read as a possessive (review round 3)."""
    assert V._POSSESSIVE_AFTER_RE.match("'S HISTORY") and V._POSSESSIVE_AFTER_RE.match("’S")


# ═════════════════════════════════════════════════════════════════════════════════════════════
# K. Everyday words grounding's 1934 dictionary cannot know (live previews, 2026-10-23/24)
# ═════════════════════════════════════════════════════════════════════════════════════════════
# An AI-chips outline was refused after its repair on `sections[4].title` "Lifespans", and a 3M
# section on "Hs": an ordinary word written with a capital (every word of a heading, the first
# word of a sentence) was read as an unknown name. Twins: an invented or real name in the same
# slot still fails, and a capital mid-sentence in prose is still a name.


def heading_issues(text, ctx, heading=True):
    return V._text_issues("t", text, cap=V.TITLE_CAP, time_words_ok=False, grounding_ctx=ctx,
                          heading=heading)


@pytest.mark.parametrize("title", [
    "Server Lifespans and Depreciation",          # the refused AI-chips outline title
    "How Hs Work", "The Three Hs of Hardware",     # the 3M preview's letter plural
    "Why Hyperscalers Keep Spending", "The Capex Cycle Behind AI Chips", "How Buybacks Fit In",
    "Tradeoffs in Custom Silicon", "The Roadmap Problem", "Spinoffs and Carveouts",
    "Payouts to Shareholders", "What a Writedown Means", "Why Hyperscalers' Budgets Grow",
    "The Lifecycle of an AI Server", "Uptime, Cooling and Power",
])
def test_everyday_words_in_a_heading_are_not_names(ctx, title):
    assert heading_issues(title, ctx) == []


@pytest.mark.parametrize("title, detail", [
    ("Server Zorblax and Depreciation", "Zorblax"),         # an invented company in the same slot
    ("Why Greenspan Raised Rates", "Greenspan"),            # a person: words + words, no compound rule
    ("How Brookfield Builds Data Centers", "Brookfield"),   # a real company off the sheet
    ("How Honeywell Grew", "Honeywell"),
    ("How Ng Invests", "Ng"),                               # two letters, but not a letter's plural
    ("Why Su Bet on Chips", "Su"),
    ("The CapEx Cycle", "CapEx"),                           # an internal capital is a brand's
    ("Why LifeSpan Matters", "LifeSpan"),
    ("Lifespanners Explained", "Lifespanners"),             # only the lemma or its plural
    ("Why Bandwidth Beat Estimates", "Bandwidth"),          # a listed company's exact name: not listed
    ("How Hyperscale Data Grew", "Hyperscale"),
])
def test_a_name_in_a_heading_still_fails(ctx, title, detail):
    out = heading_issues(title, ctx)
    assert Issue_at(out, "t", "ungrounded_entity") and detail in {i.detail for i in out}, out


def test_a_person_pair_in_a_heading_still_fails(ctx):
    assert "person_named" in codes(heading_issues("Why Lisa Su Bet on Chips", ctx))


HEADING_FIELDS = ("title", "tagLabel", "keyHighlights[0].title", "sections[3].title",
                  "sections[4].title")


def test_the_refused_outline_title_passes_in_a_whole_article():
    """Every heading slot. The short ones ("The Capex Trap": two content words, the word not
    first) pass only because the field is a heading — prose would read the capital as a name."""
    article = make_article()
    article["sections"][4]["title"] = "Server Lifespans and Depreciation"
    article["sections"][3]["title"] = "The Roadmap Problem"
    article["title"] = "The Capex Trap"
    article["tagLabel"] = "CHIP CAPEX"
    article["keyHighlights"][0]["title"] = "Long Lifespans"
    out = article_issues(article, sheet=SHEET)
    assert not any(i.field in HEADING_FIELDS for i in out), out
    for short in ("The Roadmap Problem", "The Capex Trap", "Long Lifespans"):
        assert "ungrounded_entity" in para_codes(short, grounding_ctx=V.build_grounding_context(SHEET))
    article["sections"][4]["title"] = "Server Zorblax and Depreciation"
    article["sections"][3]["title"] = "The Zorblax Problem"
    article["title"] = "The Zorblax Trap"
    article["tagLabel"] = "CHIP ZORBLAX"
    article["keyHighlights"][0]["title"] = "Long Zorblax"
    out = article_issues(article, sheet=SHEET)
    for f in HEADING_FIELDS:
        assert Issue_at(out, f, "ungrounded_entity"), (f, out)


@pytest.mark.parametrize("text", [
    "Buybacks return cash to owners.",
    "Lifespans of servers set the depreciation schedule.",
    "Servers age. Lifespans set the schedule.",
    "The three Hs of hardware matter.",                # a letter's plural anywhere
])
def test_an_everyday_word_at_a_sentence_start_passes(ctx, text):
    assert para(text, grounding_ctx=ctx) == []


@pytest.mark.parametrize("text, detail", [
    ("Investors watch Buybacks closely.", "Buybacks"),     # mid-sentence: the writer means a name
    ("Most server Lifespans run for years.", "Lifespans"),
    ("Lifespans vary, and Lifespans matter.", "Lifespans"),  # one mid-sentence use is enough
    ("Analysts expect Zorblax to grow.", "Zorblax"),
])
def test_a_capital_mid_sentence_in_prose_is_still_a_name(ctx, text, detail):
    out = para(text, grounding_ctx=ctx)
    assert detail in {i.detail for i in out if i.code == "ungrounded_entity"}, out


def test_a_title_case_list_item_reads_like_a_heading(ctx):
    block = {"type": "bulletList", "items": ["Payback Periods and Lifespans",
                                            "Payback periods and Lifespans."]}
    out = V.validate_block("b", block, section_kind="history", grounding_ctx=ctx)
    assert not any(i.field == "b.items[0]" for i in out), out
    assert Issue_at(out, "b.items[1]", "ungrounded_entity"), out


def test_a_lexicon_company_is_never_an_everyday_word(ctx, monkeypatch):
    """Even a list entry that is wrongly a company stays a name: the lexicon wins."""
    monkeypatch.setattr(V, "EVERYDAY_WORDS", V.EVERYDAY_WORDS | {"starbucks"})
    assert Issue_at(heading_issues("Why Starbucks Grew", ctx), "t", "ungrounded_entity")


def test_every_everyday_word_earns_its_place_and_names_no_one():
    given = compliance.given_names()
    for w in V.EVERYDAY_WORDS:
        assert w.isascii() and w.isalpha() and w.islower(), w
        assert not grounding.ordinary(w, frozenset(), frozenset()), f"{w}: the dictionary has it"
        assert not compliance._is_company_word(w, frozenset()), f"{w}: a lexicon company"
        assert w not in given and w not in grounding.DENIED_SURNAMES, f"{w}: a name"
    # Words a piece could mean as a franchise, company or named event stay out (module comment).
    assert not V.EVERYDAY_WORDS & {"fallout", "meltdown", "liftoff", "logjam", "gigafactory",
                                   "bandwidth", "hyperscale"}


def test_the_marketing_grounding_is_unchanged():
    """The exemption lives in the Learn validators; the marketing writer's grounding still reads
    "Lifespans" and "Hs" as unknown names."""
    mctx = grounding.build_context(["CoreWeave reported revenue of $1.2 billion."], V.corpus_vocab())
    for text, word in (("Server Lifespans and Depreciation", "Lifespans"), ("How Hs Work", "Hs")):
        assert word in {v.detail for v in grounding.check_grounding("t", text, mctx)
                        if v.code == "ungrounded_entity"}


def test_everyday_word_positions_stay_fast(ctx):
    # Lower-case words between them keep the line from reading as Title Case: every one is prose.
    words = " closely watched ".join(w.capitalize() for w in sorted(V.EVERYDAY_WORDS))
    text = ("Investors watch " + words + ". ") * 40
    t0 = time.perf_counter()
    out = V._text_issues("b", text, cap=V.BLOCK_TEXT_CAP, time_words_ok=False, grounding_ctx=ctx)
    assert time.perf_counter() - t0 < 2.0
    assert "ungrounded_entity" in codes(out)


# Review of the exemption (2026-10-24): a ticker in an ALL-CAPS label, and what counts as the start
# of a sentence.


@pytest.mark.parametrize("label", ["GS BUYBACKS", "MS", "ZS CAPEX", "TS LINEUP"])
def test_a_two_letter_ticker_in_a_caps_label_is_never_a_letters_plural(label):
    """"GS" is read in title case as "Gs", which looks like the plural of a letter."""
    article = make_article()
    article["tagLabel"] = label
    assert Issue_at(article_issues(article, sheet=SHEET), "tagLabel", "ungrounded_entity")


def test_a_letters_plural_still_passes_outside_a_caps_label():
    article = make_article()
    article["title"] = "The Three Hs of Hardware"
    article["sections"][2]["content"][0] = {"type": "paragraph",
                                            "text": "Engineers talk about the three Hs of hardware."}
    out = article_issues(article, sheet=SHEET)
    assert not any(i.field in ("title", "sections[2].content[0]") for i in out), out


@pytest.mark.parametrize("text", [
    "Servers age.\nLifespans set the schedule.",       # a line break after end punctuation
    "- Buybacks return cash to owners.",               # a bullet dash
    "Servers age. \u201cBuybacks\u201d is the usual word.",  # an opening quote
    "\u2022 Buybacks return cash to owners.",          # a bullet character
    "Servers age. (Lifespans set the schedule.)",
])
def test_punctuation_before_a_sentences_first_word_keeps_it_first(ctx, text):
    assert para(text, grounding_ctx=ctx) == []


@pytest.mark.parametrize("text", [
    "Investors watch\nBuybacks closely.",             # a line break INSIDE a sentence
    "2025: Buybacks grew.",                            # a figure or label before it
    "In short, Buybacks grew.",
    "U.S. Buybacks grew last year.",                   # an abbreviation is not a sentence end
])
def test_a_word_that_does_not_open_its_sentence_is_still_a_name(ctx, text):
    assert "Buybacks" in {i.detail for i in para(text, grounding_ctx=ctx)
                          if i.code == "ungrounded_entity"}, para(text, grounding_ctx=ctx)


# ── Claude can write Learn pieces too (llm_provider, 2026-10-30) ─────────────────────────────


@pytest.mark.parametrize("text", [
    "I am Claude.",
    "I'm Claude, and here is how the filing reads.",
    "I was built by Anthropic.",
    "I was trained by the team at Anthropic.",
    "I'm made by Anthropic to explain filings.",
])
def test_a_first_person_claude_or_anthropic_self_reference_fails(text):
    assert V.self_reference_hits(text), text
    assert "self_reference" in para_codes(text)


@pytest.mark.parametrize("text", [
    "Anthropic sells its Claude models to every large cloud.",
    "Claude, built by Anthropic, runs on rented chips.",
    "Models built by Anthropic run on these clusters.",
    "Amazon invested in Anthropic, the maker of Claude.",
])
def test_third_person_claude_and_anthropic_subject_matter_passes(text):
    assert V.self_reference_hits(text) == []


# ── 2026-10-31: an article never cites its own brief ─────────────────────────────────────────
# An ACCEPTED Claude draft read "The fact sheet gives two consecutive annual reports for each"
# (live check, 2026-10-31). The filing itself may still be named.

@pytest.mark.parametrize("text", [
    "The fact sheet gives two consecutive annual reports for each.",
    "The source says the backlog grew.",
])
def test_citing_the_fact_sheet_or_the_source_is_refused(text):
    assert "source_reference" in para_codes(text)


@pytest.mark.parametrize("text", [
    "The company's annual report describes two segments.",
    "Fees became the main source of revenue.",
])
def test_naming_the_filing_or_a_source_of_revenue_passes(text):
    assert "source_reference" not in para_codes(text)


def test_a_self_written_educational_label_is_refused_in_app():
    assert "code_owned" in para_codes("This article is for informational purposes only.")


def test_the_learn_writer_is_told_not_to_cite_its_brief():
    from app.services.learn_weekly import prompts as P

    assert "never mention the fact sheet, the source" in P.SYSTEM_BODY
    assert "fact sheet" in P.hint("source_reference")
