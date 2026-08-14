"""Chat's failure paths must fail the way the contract says, not by crashing.

WHY THIS FILE EXISTS. Two defects of the same shape shipped in `chat.py`, both invisible to
every functional test because they only fire when something upstream has ALREADY gone wrong:

1. **`route` / `reader_lens` were assigned inside the `try`** that wraps stream preparation.
   `prepare_stream_generation` touches Supabase, RAG, Gemini and FMP, so a transient blip
   there left the names unbound — and the fallback handler reads `reader_lens`, while the
   persist block reads `route`. Python evaluates call arguments before entering the callee,
   so `_record_memory_facts(user, stock_id, route)` raised `UnboundLocalError` at the CALL
   SITE, inside the persist `try`, AFTER `delivered = True`. A turn that was durably saved
   was reported to the user as *"Your answer was generated but couldn't be saved"*, and the
   `done` frame, auto-title, snapshot and follow-up suggestions were all skipped.

2. **`.single()` calls were not wrapped.** postgrest-py raises `APIError` on zero rows
   (PGRST116/406) rather than returning empty `.data`, so `GET`/`PATCH`/`DELETE
   /chat/sessions/{id}` answered a missing or other-user session with a bare **500**, and the
   adjacent `if not session.data: raise HTTPException(404)` was dead code. iOS uses that GET
   as its persistence oracle in `reconcileAfterStreamFailure`, so a 500 sent it down the
   regenerate branch — **re-charging a credit and duplicating an already-saved turn**.

Both are structural, so both guards are structural: a functional test would have to induce a
transient upstream failure at exactly the right statement to see either one.
"""

import ast
from pathlib import Path

import pytest

_CHAT = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "chat.py"


def _tree() -> ast.Module:
    return ast.parse(_CHAT.read_text(encoding="utf-8"))


def _functions(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


# ── 1. names the failure paths read must be bound before the try ─────────────

# name → the function that must bind it before its first `try`.
_MUST_BE_PREBOUND = {"route", "reader_lens"}


def _stream_generator(tree: ast.Module):
    """The nested `event_gen` inside the streaming endpoint — where both names live."""
    for fn in _functions(tree):
        if fn.name == "event_gen":
            return fn
    return None


def test_the_scan_found_the_stream_generator():
    """Guard against the guard: a rename makes every assertion below vacuous."""
    assert _stream_generator(_tree()) is not None, "event_gen not found in chat.py"


@pytest.mark.parametrize("name", sorted(_MUST_BE_PREBOUND))
def test_failure_path_names_are_bound_before_the_try(name):
    fn = _stream_generator(_tree())
    first_try = min(
        (n.lineno for n in ast.walk(fn) if isinstance(n, ast.Try)), default=None
    )
    assert first_try is not None, "event_gen has no try block — this guard has drifted"

    assigned_before = [
        t.lineno
        for n in ast.walk(fn)
        if isinstance(n, (ast.Assign, ast.AnnAssign))
        for t in ([n.target] if isinstance(n, ast.AnnAssign) else n.targets)
        if isinstance(t, ast.Name) and t.id == name and t.lineno < first_try
    ]
    assert assigned_before, (
        f"`{name}` is only assigned inside a try in event_gen. Every handler that runs "
        f"AFTER that try fails reads it, so an upstream blip makes it UnboundLocalError — "
        f"which surfaces as 'your answer couldn't be saved' on a turn that WAS saved."
    )


def test_the_prebound_route_defaults_to_degraded():
    """`degraded: True` is the honest default — nothing was classified.

    If a future edit binds `route` to a non-degraded default, `select_model` would treat an
    unclassified turn as a confident `general` classification and downgrade it to the cheap
    model. Fail-closed here means fail to the BETTER model.
    """
    fn = _stream_generator(_tree())
    first_try = min(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Try))
    for n in ast.walk(fn):
        # `route: dict = {...}` is an AnnAssign, not an Assign — accept both, or this
        # guard silently stops finding the very statement it exists to check.
        if not isinstance(n, (ast.Assign, ast.AnnAssign)) or n.lineno >= first_try:
            continue
        targets = [n.target] if isinstance(n, ast.AnnAssign) else n.targets
        if not any(isinstance(t, ast.Name) and t.id == "route" for t in targets):
            continue
        assert isinstance(n.value, ast.Dict), "the pre-bound route must be a literal dict"
        keys = {k.value for k in n.value.keys if isinstance(k, ast.Constant)}
        assert "degraded" in keys, "the pre-bound route must carry degraded=True"
        for k, v in zip(n.value.keys, n.value.values):
            if isinstance(k, ast.Constant) and k.value == "degraded":
                assert v.value is True, "the pre-bound route must be degraded=True"
        return
    pytest.fail("no pre-`try` assignment to `route` found")


# ── 2. every .single() is wrapped ────────────────────────────────────────────

def _single_calls_outside_try(tree: ast.Module):
    """`.single()` call linenos that have no enclosing `ast.Try` in the same function."""
    offenders = []
    for fn in _functions(tree):
        guarded: set = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Try):
                for child in ast.walk(node):
                    guarded.add(id(child))
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "single"
                and id(node) not in guarded
            ):
                offenders.append((fn.name, node.lineno))
    return offenders


def test_the_scan_actually_finds_single_calls():
    """Anti-vacuity: if the scan finds none at all, it can never fail."""
    tree = _tree()
    total = sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "single"
    )
    assert total >= 5, f"expected several .single() calls in chat.py, found {total}"


def test_every_single_call_is_inside_a_try():
    offenders = _single_calls_outside_try(_tree())
    assert not offenders, (
        "unwrapped .single() in chat.py at "
        + ", ".join(f"{fn}:{line}" for fn, line in offenders)
        + " — postgrest raises on zero rows, so this returns a bare 500 instead of 404 and "
        "makes the adjacent `if not session.data` check dead code."
    )
