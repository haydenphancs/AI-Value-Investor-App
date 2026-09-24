"""The persona row's display order: pinned in code, and the same in all three places.

Developer request (2026-09-23), Research tab: *"move 'growth hunter' to the second. right after
Quality Compounder."*

Measured before the fix, reproducing `GET /research/personas`'s own query read-only:
`warren_buffett, cathie_wood, bill_ackman, michael_burry, peter_lynch` — Growth Hunter LAST.
Nobody chose that. `agent_personas` has no ordering column and the endpoint had no ORDER BY, so
the list came back in Postgres heap order, and an UPDATE writes the new row version at the end
of the heap: migration 155's Growth Hunter rename (2026-08-29, the row's `updated_at`) is what
moved it from third to last. Any later UPDATE to any persona row would have reshuffled the
screen again. So the order is now a constant in code, `research._PERSONA_DISPLAY_ORDER`, and
the served list is sorted by it.

Three places must agree, or the cards reshuffle on screen:
  1. `research._PERSONA_DISPLAY_ORDER` — what the served list is sorted by;
  2. `research._FALLBACK_PERSONAS`     — served when the table read fails or is empty;
  3. iOS `AnalysisPersona.allCases`    — what PAINTS before the response lands (and the
                                         order of Settings → Default Analyst).
"""

import random
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.research import (
    _FALLBACK_PERSONAS,
    _PERSONA_DISPLAY_ORDER,
    _in_display_order,
    get_personas,
)
from app.services.agents.persona_config import PERSONA_KEYS

_RESEARCH_MODELS = (
    Path(__file__).resolve().parents[2] / "frontend/ios/ios/Models/ResearchModels.swift"
)

# The order the endpoint actually returned before the fix, measured 2026-09-23.
_MEASURED_HEAP_ORDER = ["warren_buffett", "cathie_wood", "bill_ackman", "michael_burry", "peter_lynch"]


def _row(key, name=None):
    return {"key": key, "name": name if name is not None else f"name-of-{key}"}


def _keys(rows):
    return [r.get("key") for r in rows]


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _ios_all_cases_keys() -> list:
    """`AnalysisPersona.allCases`, resolved from its static members to persona keys."""
    src = _strip_comments(_RESEARCH_MODELS.read_text(encoding="utf-8"))
    decl = re.search(r"static let allCases: \[AnalysisPersona\] = \[(.*?)\]", src, re.S)
    assert decl, "AnalysisPersona.allCases not found — this scan has drifted"
    members = re.findall(r"\.(\w+)", decl.group(1))
    key_of = dict(re.findall(r'static let (\w+) = AnalysisPersona\(\s*key: "([^"]+)"', src))
    missing = [m for m in members if m not in key_of]
    assert not missing, f"allCases names members with no `key:` declaration: {missing}"
    return [key_of[m] for m in members]


class _FakeSupabase:
    """Just enough of the Supabase query chain `get_personas` uses."""

    def __init__(self, rows=None, raises=None):
        self._rows, self._raises = rows, raises

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        if self._raises:
            raise self._raises
        return SimpleNamespace(data=self._rows)


# ── The order itself ────────────────────────────────────────────────────────


def test_growth_hunter_is_second_right_after_quality_compounder():
    assert _PERSONA_DISPLAY_ORDER[0] == "warren_buffett", "Quality Compounder is no longer first"
    assert _PERSONA_DISPLAY_ORDER[1] == "peter_lynch", (
        "Growth Hunter is no longer second — the developer asked for it right after "
        "Quality Compounder"
    )
    names = {p["key"]: p["name"] for p in _FALLBACK_PERSONAS}
    assert names["warren_buffett"] == "The Quality Compounder"
    assert names["peter_lynch"] == "The Growth Hunter"


def test_the_order_covers_every_persona_exactly_once():
    """A persona missing from the order still renders (after the known ones), but silently
    at the END — the same class of accident this constant exists to prevent."""
    assert len(_PERSONA_DISPLAY_ORDER) == len(set(_PERSONA_DISPLAY_ORDER)), "duplicate key"
    assert set(_PERSONA_DISPLAY_ORDER) == set(PERSONA_KEYS), (
        "the display order and the persona registry disagree — a persona was added or "
        "removed without placing it on the row"
    )


# ── The three places agree ──────────────────────────────────────────────────


def test_the_fallback_list_is_in_display_order():
    assert _keys(_FALLBACK_PERSONAS) == list(_PERSONA_DISPLAY_ORDER), (
        "_FALLBACK_PERSONAS is out of order — a table-read failure would reorder the row"
    )


def test_ios_all_cases_match_the_display_order():
    """`allCases` paints first; the fetched list replaces it a moment later. If the two orders
    differ the user watches the cards swap places on every Research-tab load."""
    assert _ios_all_cases_keys() == list(_PERSONA_DISPLAY_ORDER), (
        "iOS AnalysisPersona.allCases is not in _PERSONA_DISPLAY_ORDER, so the persona cards "
        "visibly reshuffle when GET /research/personas lands"
    )


# ── The sort ────────────────────────────────────────────────────────────────


def test_the_measured_heap_order_is_resorted():
    rows = [_row(k) for k in _MEASURED_HEAP_ORDER]
    assert _keys(_in_display_order(rows)) == list(_PERSONA_DISPLAY_ORDER)


def test_the_result_does_not_depend_on_row_order():
    """The whole point: whatever order Postgres hands back, the screen is the same."""
    rows = [_row(k) for k in _PERSONA_DISPLAY_ORDER] + [_row("zeta_new", "The Zeta"),
                                                        _row("alpha_new", "The Alpha")]
    expected = _keys(_in_display_order(rows))
    rng = random.Random(20260923)
    for _ in range(25):
        shuffled = rows[:]
        rng.shuffle(shuffled)
        assert _keys(_in_display_order(shuffled)) == expected


def test_an_unknown_persona_lands_after_the_known_ones_by_name():
    rows = [_row("zeta_new", "The Zeta"), _row("michael_burry"), _row("alpha_new", "The Alpha"),
            _row("warren_buffett")]
    assert _keys(_in_display_order(rows)) == [
        "warren_buffett", "michael_burry", "alpha_new", "zeta_new"]


def test_malformed_rows_sort_as_unknown_instead_of_raising():
    """This runs on every Research-tab load; one bad row must not 500 the persona list."""
    rows = [{"name": "No Key"}, {"key": None, "name": None}, {"key": "peter_lynch", "name": None},
            _row("warren_buffett"), {}]
    out = _in_display_order(rows)
    assert len(out) == len(rows)
    assert _keys(out)[:2] == ["warren_buffett", "peter_lynch"]


def test_empty_and_duplicate_inputs():
    assert _in_display_order([]) == []
    first, second = _row("peter_lynch", "A"), _row("peter_lynch", "A")
    out = _in_display_order([first, second])
    assert out[0] is first and out[1] is second, "equal rows must keep their relative order"


def test_sorting_never_mutates_its_input():
    """`get_personas` passes the module-level `_FALLBACK_PERSONAS` straight in, so an in-place
    sort would reorder that shared constant under every later request.

    ⚠️ Deliberately an OUT-OF-ORDER input. The first version of this test sorted
    `_FALLBACK_PERSONAS` itself and survived an in-place-sort mutation: that list is already
    in display order, so sorting it in place changes nothing anyone can see."""
    rows = [_row(k) for k in _MEASURED_HEAP_ORDER]
    snapshot = list(rows)
    out = _in_display_order(rows)
    assert rows == snapshot, "_in_display_order reordered its argument in place"
    assert out is not rows, "_in_display_order handed back its argument instead of a new list"


# ── The endpoint applies it on every path ───────────────────────────────────


@pytest.mark.asyncio
async def test_the_endpoint_serves_the_table_in_display_order():
    rows = [_row(k) for k in _MEASURED_HEAP_ORDER]
    served = await get_personas(supabase=_FakeSupabase(rows=rows))
    assert _keys(served) == list(_PERSONA_DISPLAY_ORDER), (
        "GET /research/personas serves raw row order again — Growth Hunter goes back to "
        "wherever the last UPDATE left its row"
    )


@pytest.mark.asyncio
async def test_the_endpoint_fallbacks_are_in_display_order():
    empty = await get_personas(supabase=_FakeSupabase(rows=[]))
    failed = await get_personas(supabase=_FakeSupabase(raises=RuntimeError("table read failed")))
    assert _keys(empty) == list(_PERSONA_DISPLAY_ORDER)
    assert _keys(failed) == list(_PERSONA_DISPLAY_ORDER)
    assert _keys(_FALLBACK_PERSONAS) == list(_PERSONA_DISPLAY_ORDER), "a fallback path mutated it"


# ── Anti-vacuity ────────────────────────────────────────────────────────────


def test_the_ios_scan_reads_code_not_the_comment_above_it():
    """The `allCases` doc comment names `_PERSONA_DISPLAY_ORDER` and the order in prose. The
    scan must resolve the real array literal — five members, each mapped through its own
    `key:` declaration — not anything the comment says."""
    keys = _ios_all_cases_keys()
    assert len(keys) == 5 and set(keys) == set(PERSONA_KEYS)
    stripped = _strip_comments("// static let allCases: [AnalysisPersona] = [.x]\nlet y = 1")
    assert "allCases" not in stripped and "let y = 1" in stripped
