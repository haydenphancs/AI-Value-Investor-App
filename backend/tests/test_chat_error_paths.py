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


# ── 3. the turn-cost frame must report SETTLED state, and reach the client ───
#
# `credits` is the only thing that tells a user a turn was free or refunded, and the only
# thing that keeps the client's credit balance from going stale — chat is the one metered
# surface that never refreshed it. Both failure modes here are ordering bugs that no
# functional test would catch, because the frame would still be emitted and still decode:
# it would just carry the WRONG answer, or arrive after the client stopped reading.


def _sse_events_in(fn) -> list[tuple[int, str]]:
    """(lineno, event-name) for every `yield _sse("<literal>", ...)` in `fn`."""
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Yield) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Name) and call.func.id == "_sse"):
            continue
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
            out.append((node.lineno, call.args[0].value))
    return sorted(out)


def _first_lineno(fn, attr: str):
    """Line of the first call to `<something>.attr(...)` in `fn`, or None."""
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr):
            return node.lineno
    return None


def test_the_scan_finds_the_stream_frames():
    """Guard against the guard: no frames found makes every assertion below vacuous."""
    events = _sse_events_in(_stream_generator(_tree()))
    assert events, "no `yield _sse(...)` found in event_gen — this guard has drifted"
    assert "done" in {name for _, name in events}


def test_the_credits_frame_is_emitted_before_done():
    """It must actually go out, and go out before the terminal frame.

    iOS holds the `credits` payload and applies it when `done` lands. Emitted after `done`
    the client has already finished the turn and drops it — the chip never renders and the
    balance stays stale, with nothing anywhere reporting a problem.
    """
    events = _sse_events_in(_stream_generator(_tree()))
    names = [name for _, name in events]
    assert "credits" in names, (
        "the streaming endpoint no longer emits a `credits` frame — a free or refunded turn "
        "is invisible to the user and the client balance goes stale"
    )
    credits_at = max(ln for ln, name in events if name == "credits")
    done_at = max(ln for ln, name in events if name == "done")
    assert credits_at < done_at, (
        f"`credits` (line {credits_at}) must be yielded before `done` (line {done_at}); "
        "iOS applies the held payload when `done` arrives and drops anything after it"
    )


def test_the_credits_frame_reports_state_after_settlement():
    """The frame must be built AFTER the refund/grant decisions, not before.

    `cost_frame()` reads `outcome`, `credits` and `balance` off the quota, and all three
    are only correct once `refund_once` / `on_delivered` have run. Hoisting the yield above
    them — or moving the settlement down — would report every refunded turn as `charged`
    and hand the client a pre-refund balance. Everything still works; it just lies.
    """
    fn = _stream_generator(_tree())
    events = _sse_events_in(fn)
    credits_at = max(ln for ln, name in events if name == "credits")

    settled_at = _first_lineno(fn, "on_delivered")
    assert settled_at is not None, "event_gen no longer settles the quota — this guard has drifted"
    assert settled_at < credits_at, (
        f"`quota.on_delivered()` (line {settled_at}) must run before the `credits` frame "
        f"(line {credits_at}), or the frame reports pre-settlement state"
    )

    attached_at = None
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_attach_turn_cost"):
            attached_at = node.lineno
            break
    assert attached_at is not None, (
        "event_gen no longer persists the turn cost — the chip would show live but vanish "
        "on a history reload"
    )
    assert attached_at < credits_at
