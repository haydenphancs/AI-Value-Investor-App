"""
Parity guard for the Money Moves read-along pipeline.

Read-along timings are produced by force-aligning the NARRATED audio against a reconstructed
transcript. Two hand-maintained functions in two different scripts must therefore agree on which
content-block types are spoken and in what order:

  - `generate_money_moves_audio.narration_blocks`  — builds the text handed to TTS (what is SPOKEN).
  - `align_money_moves_audio.collect_units`         — builds the word stream fed to the CTC aligner
                                                      (what the timings are computed AGAINST).

If a new spoken block type is ever added to `narration_blocks` (e.g. a `numberedList`) without also
teaching `collect_units` to emit it, the aligner's word list becomes a strict subset of the audio's
words; `align_word_spans` then stretches the shorter list monotonically across the whole waveform and
EVERY sentence after the divergence gets the wrong start/end — silently, with no crash and no test to
catch it. The Journey pipeline has an analogous seeder↔iOS key-set parity test
(`test_journey_schema_parity.py`); Money Moves had none. This adds it.

Implemented as a SOURCE-level check (regex over the two functions) — deliberately, so the suite does
not import the heavy torchaudio dependency `align_money_moves_audio` pulls in via `_forced_align`.
"""

from __future__ import annotations

import re
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_GENERATE_PY = _SCRIPTS / "generate_money_moves_audio.py"
_ALIGN_PY = _SCRIPTS / "align_money_moves_audio.py"

# The content-block `type` discriminators the pipeline understands (mirrors the iOS
# ArticleSectionContentDTO switch and the DB/JSON authoring). `chart` is display-only and never
# narrated, so neither spoken function should reference it.
_KNOWN_SPOKEN_TYPES = {"paragraph", "subheading", "quote", "callout", "bulletList"}


def _function_source(src: str, name: str) -> str:
    """Return the body text of `def name(...)` up to the next top-level `def`/module constant."""
    m = re.search(rf"\ndef {re.escape(name)}\b", src)
    assert m, f"function {name!r} not found — did it move/rename?"
    start = m.start()
    nxt = re.search(r"\ndef \w+", src[start + 1 :])
    return src[start : start + 1 + nxt.start()] if nxt else src[start:]


def _body_text_types(src: str) -> set[str]:
    """Resolve the module-level `_BODY_TEXT_TYPES = (...)` tuple in align_money_moves_audio.py."""
    m = re.search(r"_BODY_TEXT_TYPES\s*=\s*\(([^)]*)\)", src)
    return set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()


def _handled_block_types(region: str, body_text_types: set[str]) -> set[str]:
    """Every content-block `type` string a function branches on.

    Catches `kind == "x"`, `kind in ("x", "y")`, and `kind in _BODY_TEXT_TYPES` (resolved). Uses the
    `kind` variable name both functions bind `block.get("type")` to — a rename there would (correctly)
    fail the assertion that the two still agree.
    """
    types: set[str] = set()
    types |= set(re.findall(r'kind\s*==\s*"([^"]+)"', region))
    for grp in re.findall(r"kind\s+in\s+\(([^)]*)\)", region):
        types |= set(re.findall(r'"([^"]+)"', grp))
    if re.search(r"kind\s+in\s+_BODY_TEXT_TYPES", region):
        types |= body_text_types
    return types


def _narration_types() -> set[str]:
    src = _GENERATE_PY.read_text()
    return _handled_block_types(_function_source(src, "narration_blocks"), set())


def _alignment_types() -> set[str]:
    src = _ALIGN_PY.read_text()
    return _handled_block_types(_function_source(src, "collect_units"), _body_text_types(src))


def test_narration_and_alignment_handle_the_same_block_types():
    """The core guard: the SPOKEN block types and the ALIGNED block types must be identical."""
    narration = _narration_types()
    alignment = _alignment_types()
    assert narration == alignment, (
        "Money Moves narration_blocks and collect_units disagree on spoken block types "
        f"(only-spoken={narration - alignment}, only-aligned={alignment - narration}) — "
        "the read-along timings would desync from the audio."
    )


def test_both_cover_the_known_spoken_type_set():
    """Pin the absolute set too, so a type dropped from BOTH sides is still flagged."""
    assert _narration_types() == _KNOWN_SPOKEN_TYPES
    assert _alignment_types() == _KNOWN_SPOKEN_TYPES


def test_bulletlist_is_handled_on_both_sides():
    """bulletList is the one type whose per-item nesting (`itemsReadAlong`) makes a drop especially
    easy to miss — assert it explicitly on both sides."""
    assert "bulletList" in _narration_types()
    assert "bulletList" in _alignment_types()
