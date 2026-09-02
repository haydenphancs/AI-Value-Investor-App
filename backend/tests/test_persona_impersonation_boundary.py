"""The anti-impersonation clause must stay single-sourced and in every method voice.

"Apply the method; do not speak as, or claim to be, any real investor." is the sentence
that keeps a persona a description of a METHOD rather than an appropriation of a person.
It is the in-prompt half of the decision migration 103 made in the UI layer:

    Describing the documented METHOD is fine; naming the feature after the person is the
    part that creates the claim.

and it is what makes Terms of Use section 3 true in the product -- "investor 'personas'
and similar features are educational simulations. They do not represent the actual views,
statements, or endorsement of any real person."

Until now it was FIVE INDEPENDENT COPY-PASTES with zero coverage: `grep "do not speak as"
tests/` returned nothing, so deleting it from one prompt broke no test. These tests close
that gap, and they are also the regression guard for the refactor that introduced
`method_opening()` -- `test_the_shared_opening_helper_is_byte_exact` fails if the rendered
prompt drifts by a single character.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.agents.persona_config import (
    IMPERSONATION_BOUNDARY,
    PERSONA_KEYS,
    get_persona_config,
    method_opening,
)

_SERVICES = Path(__file__).resolve().parents[1] / "app/services"
_CLAUSE = "do not speak as, or claim to be"

# The boundary must appear in the OPENING sentence, not buried at the bottom of a 6,000
# character prompt: it is there to shape the voice, and instructions the model reads last
# do not shape what it has already decided the voice is.
_OPENING_WINDOW = 400


def _rel(path: Path) -> str:
    return str(path.relative_to(_SERVICES))


def test_the_boundary_text_is_what_we_think_it_is():
    assert IMPERSONATION_BOUNDARY == (
        "Apply the method; do not speak as, or claim to be, any real investor."
    )
    assert not IMPERSONATION_BOUNDARY.startswith(" ")
    assert not IMPERSONATION_BOUNDARY.endswith(" ")


def test_the_clause_is_single_sourced():
    """Anti-drift twin of `test_the_guards_are_still_single_sourced`.

    A sixth copy-paste would satisfy every other test here while silently diverging from
    the real sentence -- which is exactly the state this file was written to end.
    """
    hits = [
        _rel(path)
        for path in _SERVICES.rglob("*.py")
        if _rel(path) != "agents/persona_config.py" and _CLAUSE in path.read_text()
    ]
    assert not hits, (
        f"the impersonation boundary is duplicated in {hits} — "
        "import IMPERSONATION_BOUNDARY (or call method_opening) instead"
    )


def test_the_scan_is_not_vacuous():
    """If the clause stops appearing in persona_config at all, the scan above passes
    trivially and proves nothing. Pin that it is really there, once per persona."""
    src = (_SERVICES / "agents/persona_config.py").read_text()
    assert src.count(_CLAUSE) >= 1
    assert len(PERSONA_KEYS) == 5


@pytest.mark.parametrize("key", sorted(PERSONA_KEYS))
def test_every_persona_prompt_carries_the_boundary(key: str):
    assert IMPERSONATION_BOUNDARY in get_persona_config(key).system_prompt


@pytest.mark.parametrize("key", sorted(PERSONA_KEYS))
def test_the_boundary_sits_in_the_opening_sentence(key: str):
    prompt = get_persona_config(key).system_prompt
    # The identity rule is prepended by __post_init__, so measure from the method opening.
    body = prompt[prompt.index("You are Cay AI applying the "):]
    assert body.index(IMPERSONATION_BOUNDARY) < _OPENING_WINDOW


@pytest.mark.parametrize("key", sorted(PERSONA_KEYS))
def test_every_persona_uses_the_shared_opening_formula(key: str):
    prompt = get_persona_config(key).system_prompt
    assert "You are Cay AI applying the " in prompt
    assert " method: " in prompt


def test_the_shared_opening_helper_is_byte_exact():
    """The regression guard for the method_opening() refactor.

    Every persona opening must be reproducible from the helper alone. If someone edits a
    prompt's first sentence by hand, or the helper's spacing drifts, this fails.
    """
    out = method_opening(
        "QUALITY COMPOUNDER",
        "the classic quality-and-moat school of value investing associated with Warren "
        "Buffett, analyzing a company as a potential decades-long holding.",
    )
    prompt = get_persona_config("warren_buffett").system_prompt
    assert out in prompt
    assert prompt[prompt.index("You are Cay AI applying the "):].startswith(out)


def test_the_helper_joins_school_and_boundary_with_one_space():
    out = method_opening("X", "a school sentence.")
    assert out == (
        "You are Cay AI applying the X method: a school sentence. "
        + IMPERSONATION_BOUNDARY
    )
    assert "  " not in out
