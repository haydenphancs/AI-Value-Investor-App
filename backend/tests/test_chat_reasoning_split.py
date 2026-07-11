"""Unit tests for ReasoningStreamSplitter — splitting a streamed generation into a reasoning
preamble (before ===ANSWER===) and the answer. Pure state machine; no network."""

from __future__ import annotations

from app.services.chat_service import ReasoningStreamSplitter


def _drive(deltas):
    """Feed deltas, collect emitted reasoning chunks + answer chunks, then finish()."""
    s = ReasoningStreamSplitter()
    reasoning_emitted, answer_emitted = [], []
    for d in deltas:
        rcs, ac = s.feed(d)
        reasoning_emitted.extend(rcs)
        if ac:
            answer_emitted.append(ac)
    rcs, ac = s.finish()
    reasoning_emitted.extend(rcs)
    if ac:
        answer_emitted.append(ac)
    return s, reasoning_emitted, "".join(answer_emitted)


def test_happy_path_single_delta():
    s, r, ans = _drive(["Compare ROE to margins.\n===ANSWER===\nROE is high because equity is negative."])
    assert s.reasoning == "Compare ROE to margins."
    assert r == ["Compare ROE to margins."]
    assert ans == "ROE is high because equity is negative."
    assert s.answer == ans


def test_marker_split_across_deltas():
    # The separator arrives in pieces across chunks (Gemini streams large blocks).
    s, r, ans = _drive(["Think about it.\n==", "=ANSWER=", "==\nHere is the answer.", " More."])
    assert s.reasoning == "Think about it."
    assert r == ["Think about it."]
    assert ans == "Here is the answer. More."


def test_no_marker_is_all_answer():
    # Model ignored the format → everything is the answer, reasoning empty. Answer never lost.
    text = "Just a short direct answer with no separator."
    s, r, ans = _drive([text])
    assert s.reasoning == ""
    assert r == []
    assert ans == text


def test_cap_exceeded_without_marker_flushes_as_answer():
    # A long response with no separator must still surface as the answer once the cap is hit.
    big = "x" * 900  # > default cap of 800
    s, r, ans = _drive([big])
    assert s.reasoning == ""
    assert r == []
    assert ans == big


def test_empty_reasoning_when_marker_first():
    s, r, ans = _drive(["===ANSWER===\nStraight to the answer."])
    assert s.reasoning == ""
    assert r == []                    # no empty reasoning frame emitted
    assert ans == "Straight to the answer."


def test_answer_streams_delta_by_delta_after_marker():
    s = ReasoningStreamSplitter()
    s.feed("reasoning here\n===ANSWER===\n")   # marker consumed; nothing after yet
    _, a1 = s.feed("Hello ")
    _, a2 = s.feed("world.")
    assert a1 == "Hello " and a2 == "world."
    assert s.answer == "Hello world."
    assert s.reasoning == "reasoning here"


def test_leading_newlines_stripped_from_answer():
    s, r, ans = _drive(["reason\n===ANSWER===\n\n\nAnswer body."])
    assert ans == "Answer body."   # leading blank lines after the separator are trimmed


def test_marker_straddling_cap_boundary_not_leaked():
    """Reasoning exceeds the cap AND the separator straddles the flush boundary across deltas: the
    literal marker must NOT leak into the answer (a marker-length tail is held back on cap-flush)."""
    s = ReasoningStreamSplitter(cap=20)
    s.feed("x" * 18 + "===ANS")           # 24 chars > cap; marker incomplete → cap-flush, keep tail
    s.feed("WER===\nThe real answer")      # completes the separator against the retained tail
    s.finish()
    assert "===ANSWER===" not in s.answer   # the crux: no marker leak
    assert s.answer.endswith("The real answer")
    assert s.reasoning == "xxxxxx"          # the tail before the (late) separator
