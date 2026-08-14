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
