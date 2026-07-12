"""Tests for the chat output-guardrail scanner (Phase 5).

Monitoring, not enforcement — the endpoint LOGS these, it doesn't block. The tests pin the detection
(so a regression that starts leaking buy/sell directives or the underlying model becomes observable)
AND pin the no-false-positive contract (tradeoff/conditional language + the company 'Google' must
stay clean, so we never flag a good answer)."""

from app.services.agents.chat_guardrails import scan_answer


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
