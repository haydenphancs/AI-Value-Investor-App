"""Tests for the chat output-guardrail scanner (Phase 5).

Monitoring, not enforcement — the endpoint LOGS these, it doesn't block. The tests pin the detection
(so a regression that starts leaking buy/sell directives or the underlying model becomes observable)
AND pin the no-false-positive contract (tradeoff/conditional language + the company 'Google' must
stay clean, so we never flag a good answer)."""

import pytest

from app.services.agents.chat_guardrails import scan_answer, enforce_answer


def test_clean_answer_has_no_issues():
    assert scan_answer("Apple's P/E is 38, above the sector. Educational, not financial advice.") == []
    assert scan_answer("") == []
    assert scan_answer(None) == []   # type: ignore[arg-type]


def test_advice_directive_flagged():
    assert "advice_directive" in scan_answer("Honestly, you should buy AAPL right now.")
    assert "advice_directive" in scan_answer("I recommend buying this stock.")
    assert "advice_directive" in scan_answer("You must sell before earnings.")
    assert "advice_directive" in scan_answer("Given all that, I would buy it.")


def test_no_false_positive_on_tradeoff_language():
    assert scan_answer("The buy case rests on margins; the bear case is valuation.") == []
    assert scan_answer("Reasons someone might buy: a wide moat. Reasons for caution: a high multiple.") == []
    assert scan_answer("Some investors would consider adding on weakness.") == []


def test_identity_leak_flagged():
    assert "identity_leak" in scan_answer("I'm powered by Gemini.")
    assert "identity_leak" in scan_answer("As a large language model, I can't predict prices.")
    assert "identity_leak" in scan_answer("I was trained by Google.")


def test_bare_google_company_is_not_a_leak():
    # 'Google' the company/ticker is legitimate — only model/provider leaks count.
    assert scan_answer("Google (GOOGL) has strong ad revenue and a wide moat.") == []


def test_both_issues_detected_together():
    issues = scan_answer("As an AI, I think you should buy it.")
    assert set(issues) == {"advice_directive", "identity_leak"}


def test_no_false_positive_on_identity_substring():
    """The bug: bare substring `"as an ai" in text` fired inside 'as an aid' / 'as an aircraft' /
    'as an aim' — flagging a perfectly benign investing answer as an identity leak. Word-boundary
    matching must keep these clean while still catching the real token."""
    assert scan_answer("Treasuries can serve as an aid to managing downside risk.") == []
    assert scan_answer("Boeing sells to every major carrier as an aircraft maker.") == []
    assert scan_answer("Dollar-cost averaging works as an aim for long-term savers.") == []
    # The real leak still trips (whole-token match).
    assert "identity_leak" in scan_answer("As an AI, I can't predict prices.")
    assert "identity_leak" in scan_answer("Honestly, as an ai — I can't give a target.")


# ── enforce_answer (targeted REDACTION) ───────────────────────────────────────

def test_enforce_redacts_api_keys():
    txt = "Debug: key=AIzaSyABCDEFGHIJKLMNOPQRSTUVWX1234567 works."
    out, tags = enforce_answer(txt)
    assert "AIzaSy" not in out and "***" in out
    assert "secret_redacted" in tags


def test_enforce_redacts_openai_key_and_jwt():
    out, tags = enforce_answer(
        "token sk-ABCDEFGHIJKLMNOPQRSTUVWX and jwt eyJhbGciOiJIUzI1NiIsIn.eyJzdWIiOiIxMjM0.abcdef123456"
    )
    assert "sk-ABCDEFGHIJ" not in out
    assert "eyJhbGci" not in out
    assert "secret_redacted" in tags


def test_enforce_redacts_internal_schema_identifiers():
    out, tags = enforce_answer("It reads from chat_messages and calls search_filing_chunks with auth.uid().")
    assert "chat_messages" not in out
    assert "search_filing_chunks" not in out
    assert "auth.uid" not in out
    assert "schema_redacted" in tags


def test_enforce_redacts_self_referential_identity_to_cay_ai():
    out, tags = enforce_answer("As an AI, I was trained by Google to help you.")
    assert "as an ai" not in out.lower()
    assert "trained by google" not in out.lower()
    assert "Cay AI" in out
    assert "identity_redacted" in tags


def test_enforce_preserves_legitimate_company_mentions():
    # A user may legitimately ask about OpenAI / Gemini (the crypto exchange) / Google.
    # Bare company/product names must NOT be redacted — only self-referential leaks are.
    txt = "OpenAI is a hot pre-IPO name, Gemini is a crypto exchange, and Google (GOOGL) has a wide moat."
    out, tags = enforce_answer(txt)
    assert out == txt
    assert tags == []


def test_enforce_is_noop_on_clean_answer():
    txt = "Apple trades at 38x earnings, above its 5-year average. Educational, not financial advice."
    out, tags = enforce_answer(txt)
    assert out == txt and tags == []


def test_enforce_handles_empty_and_none():
    assert enforce_answer("") == ("", [])
    assert enforce_answer(None) == ("", [])   # type: ignore[arg-type]


def test_enforce_does_not_redact_advice_phrasing():
    # Advice-boundary stays MONITOR-only (scan_answer flags it); enforce_answer must not touch it.
    txt = "You should buy AAPL now."
    out, tags = enforce_answer(txt)
    assert out == txt and tags == []
    assert "advice_directive" in scan_answer(txt)


# ── enforce_answer FALSE-POSITIVE preservation (AI-sector finance prose) ───────
# Regression for the review findings: the identity/secret redactors previously corrupted
# legitimate answers about AI-sector companies and long hyphenated finance compounds.

def test_enforce_preserves_as_an_ai_noun_phrases():
    # "as an AI <noun>" is everyday phrasing in an AI-investing product — NOT a self-reveal.
    for txt in (
        "NVIDIA, as an AI chip maker, dominates the accelerator market.",
        "Palantir markets itself as an AI platform for enterprises.",
        "Investors treat AI as a secular growth theme.",
    ):
        out, tags = enforce_answer(txt)
        assert out == txt and tags == [], (txt, out, tags)


def test_enforce_preserves_language_model_as_topic():
    for txt in (
        "As a language model grows in parameters, training costs rise, which benefits NVDA.",
        "A large language model like GPT needs many GPUs, a tailwind for NVDA.",
    ):
        out, tags = enforce_answer(txt)
        assert out == txt and tags == [], (txt, out, tags)


def test_enforce_preserves_created_by_provider_product_statements():
    # "created/made/developed by Google/OpenAI" describes a PRODUCT — legit, not self-reveal.
    for txt in (
        "The model was created by Google DeepMind researchers.",
        "Products made by OpenAI are popular with developers.",
        "Revenue created by Google's ad business is enormous.",
    ):
        out, tags = enforce_answer(txt)
        assert out == txt and tags == [], (txt, out, tags)


def test_enforce_still_catches_trained_by_provider_even_after_self_ref_match():
    # The chained case: "As an AI, I" is redacted WITHOUT orphaning "…trained by Google".
    out, tags = enforce_answer("As an AI, I was trained by Google to help you.")
    low = out.lower()
    assert "trained by google" not in low and "as an ai" not in low
    assert "identity_redacted" in tags


def test_enforce_secret_regex_preserves_hyphenated_finance_compounds():
    for txt in (
        "A risk-averse-diversified-portfolio-strategy suits retirees.",
        "Consider a basket-of-stocks-and-bonds-allocation approach.",
    ):
        out, tags = enforce_answer(txt)
        assert out == txt and tags == [], (txt, out, tags)


# ── Suitability claims (Phase 4, monitor-only) ───────────────────────────────

@pytest.mark.parametrize("answer", [
    "This one is right for you given what you follow.",
    "That ETF is suitable for you.",
    "It fits your profile nicely.",
    "This matches your risk appetite.",
    "A great fit for you.",
    "Given your goals, this is the one.",
    "It aligns with your goals.",
    "Perfect for you.",
    "For someone like you, this is the obvious pick.",
])
def test_suitability_claims_are_flagged(answer):
    assert "suitability_claim" in scan_answer(answer)


@pytest.mark.parametrize("answer", [
    "Margins expanded 200bps year over year.",
    "Some investors weigh dividend cover before yield.",
    "The debt-to-equity ratio is 1.8x, above the sector median.",
])
def test_ordinary_analysis_is_not_flagged(answer):
    assert "suitability_claim" not in scan_answer(answer)


def test_the_compliant_refusal_also_trips_it_and_that_is_why_it_is_monitor_only():
    """Documented, accepted false positive.

    "whether it's right for you depends on circumstances I can't see" is the model
    COMPLYING with ADVICE_BOUNDARY. It trips the same phrase the violation does, which is
    precisely why this tag must never drive redaction: enforcing would corrupt the
    compliant answers while barely touching the non-compliant ones.
    """
    compliant = (
        "Whether it's right for you depends on your circumstances, which I can't see. "
        "Caydex is not a registered investment adviser."
    )
    assert "suitability_claim" in scan_answer(compliant)
    # …and enforcement leaves it completely untouched.
    redacted, _ = enforce_answer(compliant)
    assert redacted == compliant


def test_suitability_is_not_an_enforcement_class():
    """Pins the decision so nobody 'fixes' it later: enforce_answer must not redact any
    suitability phrasing."""
    for answer in ("This is right for you.", "It fits your profile."):
        redacted, _ = enforce_answer(answer)
        assert redacted == answer


# ── Inline links are model output, and iOS rendered them tappable ─────────────────────
#
# `[Verify your Caydex account](https://caydex-support.example)` in a Cay AI bubble — steered
# by a poisoned headline, a tool result, a cached brief or the user's own earlier turn — was a
# blue link that left the app for ANY scheme: a phishing page, a `tel:+1900…` dialer prompt,
# an `sms:…&body=…` pre-filled message. Cay AI cites through `sources`, never inline.


def test_markdown_links_are_reduced_to_their_label():
    from app.services.agents.chat_guardrails import enforce_answer

    text, tags = enforce_answer(
        "Apple beat. [Verify your Caydex account](https://caydex-support.example) today."
    )
    assert text == "Apple beat. Verify your Caydex account today."
    assert "link_stripped" in tags
    assert "https://" not in text


def test_phone_sms_and_autolink_targets_are_dropped():
    from app.services.agents.chat_guardrails import enforce_answer

    text, tags = enforce_answer(
        "Call [support](tel:+19005551212), [text us](sms:+1900?body=hi), "
        "see <https://evil.example/x> or tel:+19005551212."
    )
    for needle in ("tel:", "sms:", "evil.example", "19005551212"):
        assert needle not in text, text
    assert "support" in text and "text us" in text
    assert "link_stripped" in tags


def test_image_embeds_keep_only_their_alt_text():
    from app.services.agents.chat_guardrails import enforce_answer

    text, _ = enforce_answer("![tracking pixel](https://img.example/p.gif) Revenue grew 8%.")
    assert text == "tracking pixel Revenue grew 8%."


def test_ordinary_brackets_and_parentheses_survive():
    """Control: finance prose is full of `[x]` and `(y)` that are not links."""
    from app.services.agents.chat_guardrails import enforce_answer

    src = "EPS [adjusted] rose 12% (vs 9% est.); see note [2] and (b) above."
    text, tags = enforce_answer(src)
    assert text == src and "link_stripped" not in tags


def test_the_prompt_forbids_links_and_the_ios_renderer_strips_them():
    """Both halves: the model is told not to, and the renderer cannot make one tappable."""
    import re
    from pathlib import Path
    from app.services.chat_service import ChatService

    svc = ChatService.__new__(ChatService)
    instruction = svc._build_system_instruction("NORMAL", "AAPL", asset_type="STOCK")
    assert "Never include URLs, markdown links" in instruction

    swift = (Path(__file__).resolve().parents[2]
             / "frontend/ios/ios/Views/Atoms/MarkdownText.swift").read_text()
    code = "\n".join(re.sub(r"//.*$", "", l) for l in swift.splitlines())
    body = code[code.index("private struct MarkdownInline"):]
    body = body[:body.index("#Preview")]
    assert "static func stripLinks(_ text: inout AttributedString)" in body
    assert "Self.stripLinks(&parsed)" in body
    fn = body[body.index("static func stripLinks("):]
    assert re.search(r"for run in text\.runs where run\.link != nil \{\s*text\[run\.range\]\.link = nil", fn), fn


# ── Persona drift (monitor-only, 2026-10-02) ──────────────────────────────────
#
# The report chat answers in a method MODE ("Cay AI · Growth Hunter Agent"). Two drifts are
# monitored: speaking AS a real investor, and claiming holdings or trades of its own. Both
# tables matter equally — `theme_insights_service.find_banned_language` turns every
# `scan_answer` tag into a rejected theme summary, so a false positive costs real output.

_IMPERSONATION_HITS = [
    "I'm Warren Buffett, and here is how I see Apple.",
    "I am Peter Lynch.",
    "As Cathie Wood, I see a huge opportunity here.",
    "Michael Burry here — the balance sheet worries me.",
    "Michael Burry here",
    "Peter Lynch here.",
    "Speaking as Bill Ackman, the catalyst is obvious.",
    "My name is Peter Lynch and this is a fast grower.",
    "I’m Buffett, and I like moats.",              # curly apostrophe
    "As Buffett, I'd wait for a better price.",
    "I'm Mr. Buffett.",
    "I am Charlie Munger.",
    "Writing as Lynch: this is a stalwart.",
    "As Ray Dalio, my view is cautious.",
    # The comma/whitespace joint after the name, every spelling (fix pass 2026-10-02).
    "As Buffett,I'd wait.",
    "As Buffett , I'd wait.",
    "As   Buffett    I think so.",
]

_IMPERSONATION_CLEAN = [
    "GARP was popularized by Peter Lynch's books.",
    "As Peter Lynch described, the story matters.",
    "No — I'm not Peter Lynch; I'm Cay AI, an AI by Caydex.",
    "Berkshire Hathaway, led by Warren Buffett, owns Apple shares.",
    "Merrill Lynch here…",
    "Wood here is a commodity input.",
    "Peter Lynch here argues that a simple story is evidence.",   # a Book chat's natural prose
    "The method described by Peter Lynch here.",
    "Peter Lynch speaking at a conference in 1990 said much the same.",
    "Investors such as Peter Lynch, I believe, popularized the PEG ratio.",
    "I'm Cay AI by Caydex, working as the Growth Hunter Agent in this chat — an AI applying "
    "the growth-at-a-reasonable-price method this report used.",
    "No — I'm Cay AI, an AI by Caydex. Growth Hunter is a Caydex analysis style, not a real "
    "investor, and Caydex is not affiliated with or endorsed by any investor.",
    "Lynch Industries reported higher sales.",
    # A possessive or compound is third-person; a comma after "here" is Book-chat prose.
    "I am Peter Lynch's biggest fan.",
    "I'm Lynch-like in my approach.",
    "I’m Buffett’s student only in the sense that I read his letters.",   # curly
    "Peter Lynch here, in One Up On Wall Street, argues that stories matter.",
]

_HOLDINGS_HITS = [
    "I own shares of Apple.",
    "In my portfolio, this is a core position.",
    "I bought more last week.",
    "I've sold half of it.",
    "My holdings include AAPL and MSFT.",
    "I trimmed my stake in Apple.",
    "I also own Microsoft.",
    "I personally own it.",
    "I recently bought shares.",
    "I hold Apple through the cycle.",
    "I have held it for years.",
    "When I bought it, it was cheap.",
    "My track record with growers is strong.",
    "I’ve owned it since 2010.",
    "I own it.",
    # A skip word in an EARLIER sentence never excuses the next one.
    "The question is what to do. I bought more.",
    "What should we do? I sold it.",
    "Should I? I own it.",
    "If the margin holds; I hold it anyway.",
    # "hold on to" / "held onto" are holdings, not the "hold off" idiom.
    "I hold on to my shares.",
    "I held onto Apple.",
]

_HOLDINGS_CLEAN = [
    "Insiders own 0.1% of the shares.",
    "Some funds bought shares last quarter.",
    "If you own the stock, watch the margins.",
    "Berkshire owns 5% of the company.",
    "If I own the stock, what changes?",
    "What if I sold now?",
    "Phase I sold out within a week.",
    "Should I hold?",
    "Do I own enough of it?",
    "As Cay AI, I hold no positions and own nothing.",
    "I don't own any shares.",
    "I have no portfolio of my own.",
    "Shares held their gains into the close.",
    "Whether I sold or not is irrelevant to the thesis.",
    "I never owned it.",
    # Worked examples and hypotheticals.
    "Suppose I bought 100 shares at $10, then the price doubled.",
    "Imagine I own a lemonade stand.",
    "Let's say I sold half at the top.",
    "Assume I bought at the peak.",
    "Hypothetically, I sold everything.",
    # Numbered names, not a pronoun.
    "Vision Fund I sold its stake in 2021.",
    "Model I sold well.",
    "Act I held the audience.",
    # Idioms.
    "I hold off on a verdict until the filing.",
    "I still hold the view that margins matter.",
]


@pytest.mark.parametrize("answer", _IMPERSONATION_HITS)
def test_first_person_impersonation_is_flagged(answer):
    assert "persona_impersonation" in scan_answer(answer), answer


@pytest.mark.parametrize("answer", _IMPERSONATION_CLEAN)
def test_third_person_mentions_are_not_flagged(answer):
    assert "persona_impersonation" not in scan_answer(answer), answer


@pytest.mark.parametrize("answer", _HOLDINGS_HITS)
def test_first_person_holdings_are_flagged(answer):
    assert "first_person_holdings" in scan_answer(answer), answer


@pytest.mark.parametrize("answer", _HOLDINGS_CLEAN)
def test_ordinary_holdings_language_is_not_flagged(answer):
    assert "first_person_holdings" not in scan_answer(answer), answer


@pytest.mark.parametrize("answer", _IMPERSONATION_HITS + _HOLDINGS_HITS)
def test_persona_tags_are_monitor_only(answer):
    """Never redacted: rewriting a first-person sentence mid-answer would corrupt it."""
    redacted, tags = enforce_answer(answer)
    assert redacted == answer
    assert not {"persona_impersonation", "first_person_holdings"} & set(tags)


def test_the_persona_tags_add_nothing_to_an_existing_tag():
    """The new patterns run on their own folded copy; an answer the old tags flagged is
    flagged exactly as before, with the persona tags only ever appended."""
    assert scan_answer("I'm an AI") == ["identity_leak"]
    assert scan_answer("You should buy it.") == ["advice_directive"]
    assert scan_answer("You should buy it. I own it.") == ["advice_directive", "first_person_holdings"]


def test_a_long_whitespace_run_after_a_name_stays_linear():
    """`\\s*,?\\s+` let a whitespace run split many ways (2 s at 20k spaces, quadratic);
    scan_answer runs synchronously on the event loop, so the joint must match one way only."""
    import time

    for text in ("as buffett" + " " * 50_000 + "x",
                 "as buffett" + " " * 25_000 + "," + " " * 25_000 + "x",
                 "peter lynch" + " " * 50_000 + "here" + " " * 50_000 + "x",
                 "i am" + " " * 50_000 + "x"):
        started = time.perf_counter()
        scan_answer(text)
        assert time.perf_counter() - started < 1.0, text[:16]


def test_scan_answer_still_never_raises_on_garbage():
    for value in (None, "", "   ", "I" * 100_000, "i " * 50_000, "’" * 10_000):
        assert isinstance(scan_answer(value), list)


# ── The endpoint wires the persona monitor on BOTH output channels (source scan, AST) ──
#
# The reasoning channel is shown in the thinking card and persisted; with a report chat's mode
# voice it is the likeliest place for "Peter Lynch would…" to surface, and it used to be
# enforced but never scanned. AST, so comments (which name every token) cannot satisfy it.

def _chat_endpoint_tree():
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "chat.py")
    return ast.parse(src.read_text(encoding="utf-8"))


def _calls(tree, name):
    import ast

    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]


def test_the_streamed_reasoning_is_scanned_not_only_enforced():
    import ast

    scanned = [c for c in _calls(_chat_endpoint_tree(), "scan_answer")
               if c.args and isinstance(c.args[0], ast.Name) and c.args[0].id == "reasoning_text"]
    assert scanned, "chat.py no longer runs scan_answer over the streamed reasoning"


def test_every_guardrail_log_line_carries_the_persona():
    import ast

    lines = []
    for node in ast.walk(_chat_endpoint_tree()):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "warning" and node.args):
            fmt = node.args[0]
            parts = [fmt] if isinstance(fmt, ast.Constant) else (
                [v for v in ast.walk(fmt) if isinstance(v, ast.Constant)])
            text = "".join(str(p.value) for p in parts if isinstance(p.value, str))
            if text.startswith("Chat guardrail ("):
                lines.append(text)
    assert len(lines) == 3, lines        # send, stream, stream reasoning
    for text in lines:
        assert "persona=%s" in text, text


# ── Report chat's web search (2026-10-02): the code-authored caveat is not a guardrail event ──
#
# The caveat is appended AFTER enforcement, but a model-written copy of it (or an attributed
# web answer) passes through `enforce_answer` / `scan_answer` first. Neither may redact it or
# raise a monitor flag — a false flag on every web turn would bury the real ones in the log.

def test_the_web_caveat_and_an_attributed_web_answer_pass_the_guardrails_untouched():
    from app.services.chat_security import web_caveat_line

    for text in (
        web_caveat_line("2026-09-22"),
        web_caveat_line(None),
        "Reuters, Sep 30, 2026: the DOJ case against Apple advanced. The report, dated Sep 22, "
        "2026, rates the legal risk as moderate; the web figure and the report figure differ.",
        "A web search found nothing usable for this question, so this answer is from the report.",
    ):
        cleaned, enforced = enforce_answer(text)
        assert cleaned == text and enforced == [], (text, enforced)
        assert scan_answer(text) == [], text
