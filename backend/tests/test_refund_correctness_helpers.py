"""The three refund-correctness helpers behind every REFUND LEAK alert — none pinned directly.

WHY THIS FILE EXISTS
--------------------
Generate Analysis charges 20 credits UP FRONT and gives them back on failure through a one-shot
compare-and-set on `research_reports.is_refunded`. Once that CAS is spent nothing retries, so
three small helpers are all that decides whether a human is paged about a stranded charge. A
grep of `tests/` for their names shows how little holds them in place:

  * `research_reconciliation_service._worst_case_queue_drain_seconds` — ZERO tests. It sets the
    window after which a never-started row is treated as orphaned and refunded. Every test that
    touches the derived `RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS` uses it symbolically
    (`+ 60`, `+ 100`), so rewriting the function to `return 0` — collapsing the window to the
    1-hour floor — stays green. At saturation a legitimately queued report (back of a 150-deep
    backlog draining 8-wide at 600 s each waits three hours) would then be refunded WHILE STILL
    QUEUED: the user is charged nothing, the pipeline still runs, and we pay Gemini and FMP for
    a report that is then stamped failed.
  * `credit_service.refund_did_not_happen` — the one predicate all three report call sites use.
    Its docstring promises a THREE-shape contract (None / migration-142 dict / pre-142 int), and
    the suite pins one line of it directly (`capped_to_zero`, in `test_credit_pool_isolation`)
    and one more through a call site (`no_matching_debit`). `no_credits_row`, the transport
    fault, the legacy integer and every benign outcome are asserted nowhere, so shrinking
    `REFUND_FAILURE_OUTCOMES` by that one name stays green.
  * `research.py._outcome_name` — ZERO tests. It is the `outcome=%s` on both endpoint-side
    REFUND LEAK lines. Mutate it and the alert still fires but names the wrong thing, so the
    manual correction is made against the wrong diagnosis. Silently.

Pure functions plus AST-bounded wiring guards. No Supabase, no network: `settings` is patched
in place — it is the same object the subject reads at call time — and `CreditService()` is
never constructed.
"""
from __future__ import annotations

import ast
import heapq
import inspect
import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.research_reconciliation_service as recon
from app.api.v1.endpoints import research as research_endpoint
from app.config import settings
from app.services import credit_service
from app.services.credit_service import (
    REFUND_FAILURE_OUTCOMES,
    REFUND_SETTLED_OUTCOMES,
    refund_did_not_happen,
)

# The full outcome vocabulary of `refund_credits` as of migration 142, split the way the
# predicate must split it. Spelled out as LITERALS on purpose: parametrising over
# `REFUND_FAILURE_OUTCOMES` itself would pass after someone shrinks it.
_OWED_OUTCOMES = ("no_matching_debit", "no_credits_row", "capped_to_zero")
_BENIGN_OUTCOMES = ("refunded", "already_refunded", "guest", "invalid")
_RPC_OUTCOMES = _OWED_OUTCOMES + _BENIGN_OUTCOMES

# Captured at collection time — the same moment `recon` computed
# `RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS` from these three values.
_IMPORT_TIME_CAPS = (
    settings.MAX_CONCURRENT_AGENT_RUNS,
    settings.MAX_GLOBAL_INFLIGHT_REPORTS,
    settings.RESEARCH_PIPELINE_TIMEOUT_SECONDS,
)


@pytest.fixture(autouse=True)
def _restore_caps():
    """`RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS` is computed ONCE at import from three settings
    the subject then re-reads on every call. Each test that retunes them goes through
    `monkeypatch`, which restores on teardown; this is the belt to that brace, so a failure
    mid-test can never leave the derived-constant assertions below comparing against a
    retuned `settings` object."""
    yield
    (
        settings.MAX_CONCURRENT_AGENT_RUNS,
        settings.MAX_GLOBAL_INFLIGHT_REPORTS,
        settings.RESEARCH_PIPELINE_TIMEOUT_SECONDS,
    ) = _IMPORT_TIME_CAPS


def _caps(monkeypatch, *, slots=8, inflight=150, timeout=600):
    """Retune the three caps the drain formula reads. Patched on the `settings` INSTANCE —
    the module-level `from app.config import settings` in the subject binds that object, and
    the function reads its attributes at call time, so the instance is the live binding."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_AGENT_RUNS", slots)
    monkeypatch.setattr(settings, "MAX_GLOBAL_INFLIGHT_REPORTS", inflight)
    monkeypatch.setattr(settings, "RESEARCH_PIPELINE_TIMEOUT_SECONDS", timeout)


def _last_start_second(slots: int, inflight: int, timeout: int) -> int:
    """Independent oracle: simulate `inflight` reports admitted at once, draining through
    `max(1, slots)` agent slots, every run lasting the full timeout ceiling. Returns the second
    at which the LAST report in the backlog gets a slot — the longest any legitimately queued
    row can sit with `processing_started_at` NULL."""
    free = [0] * max(1, slots)
    heapq.heapify(free)
    last = 0
    for _ in range(max(0, inflight)):
        last = heapq.heappop(free)
        heapq.heappush(free, last + timeout)
    return last


# ── 1. `_worst_case_queue_drain_seconds` — the orphan-detection window ───────────────────

@pytest.mark.parametrize("inflight", [0, 1, 8, 9, 17, 150])
@pytest.mark.parametrize("slots", [1, 3, 8, 16])
def test_the_formula_equals_a_simulated_queue_drain(monkeypatch, slots, inflight):
    """Anti-vacuity, and the correctness argument in one: the closed form must agree with a
    brute-force drain at every grid point, including the batch boundaries (8 → 9, 16 → 17)
    where an off-by-one in `ceil` would under-shoot by a whole 600 s batch and refund the
    back-of-queue report before it starts. A constant fails everywhere but one cell."""
    _caps(monkeypatch, slots=slots, inflight=inflight, timeout=600)
    assert recon._worst_case_queue_drain_seconds() == _last_start_second(slots, inflight, 600)


def test_the_shipped_defaults_derive_the_documented_window(monkeypatch):
    """8 slots, 150 in flight, 600 s ceiling: 142 queued behind the first batch, 18 batches,
    three hours. Plus the 10-minute margin that is the "~3.2h" `research.py` cites above its
    `create_task` — the number a reviewer will look for when the comment and the code drift."""
    _caps(monkeypatch, slots=8, inflight=150, timeout=600)
    assert recon._worst_case_queue_drain_seconds() == 18 * 600 == 10800
    assert max(3600, recon._worst_case_queue_drain_seconds() + 600) == 11400


@pytest.mark.parametrize("slots", [0, -3])
def test_a_zero_or_negative_slot_count_falls_to_one_slot(monkeypatch, slots):
    """A misconfigured semaphore width must not take the import down with a ZeroDivisionError
    (this runs at module load, so that would be the whole app) and must not produce a SMALLER
    window: one slot is the slowest possible drain, which is the conservative answer."""
    _caps(monkeypatch, slots=slots, inflight=150, timeout=600)
    guarded = recon._worst_case_queue_drain_seconds()
    _caps(monkeypatch, slots=1, inflight=150, timeout=600)
    assert guarded == recon._worst_case_queue_drain_seconds() == 149 * 600


@pytest.mark.parametrize("inflight", [8, 3, 0, -1])
def test_no_backlog_means_no_wait(monkeypatch, inflight):
    """When nothing can ever queue behind the first batch the derived drain is zero — never
    negative, which would shave time off the floor. Note `MAX_GLOBAL_INFLIGHT_REPORTS = 0`
    DISABLES the admission gate (config.py), so the backlog is then unbounded and the
    derivation says 0 anyway: the 3600 s floor in `RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS`
    is the only thing standing between that setting and an instant orphan sweep."""
    _caps(monkeypatch, slots=8, inflight=inflight, timeout=600)
    assert recon._worst_case_queue_drain_seconds() == 0


def test_retuning_the_caps_moves_the_window_the_right_way(monkeypatch):
    """The comment above the function promises the window survives a retune "e.g. raised at
    Gemini Tier 2". More slots drain faster (window shrinks), a deeper backlog or a longer
    ceiling waits longer (window grows) — pinned so a swapped operand cannot invert one."""
    _caps(monkeypatch, slots=8, inflight=150, timeout=600)
    base = recon._worst_case_queue_drain_seconds()

    _caps(monkeypatch, slots=16, inflight=150, timeout=600)
    assert recon._worst_case_queue_drain_seconds() == base // 2, "doubling the slots should halve the drain"

    _caps(monkeypatch, slots=8, inflight=300, timeout=600)
    assert recon._worst_case_queue_drain_seconds() > base

    _caps(monkeypatch, slots=8, inflight=150, timeout=1200)
    assert recon._worst_case_queue_drain_seconds() == 2 * base


def test_the_abandon_window_is_derived_from_the_formula(monkeypatch):
    """The module constant is `max(3600, drain + 600)` and must EXCEED the drain — the whole
    guarantee is "a still-queued report is never false-refunded". Evaluated under the caps
    that were in force when the constant was computed, so a leaked retune elsewhere in the
    process cannot make this compare apples to oranges."""
    _caps(monkeypatch, slots=_IMPORT_TIME_CAPS[0], inflight=_IMPORT_TIME_CAPS[1], timeout=_IMPORT_TIME_CAPS[2])
    drain = recon._worst_case_queue_drain_seconds()
    assert recon.RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS == max(3600, drain + 600)
    assert recon.RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS > drain
    assert recon.RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS >= 3600


def _module_node(module, predicate):
    tree = ast.parse(Path(inspect.getfile(module)).read_text())
    hits = [n for n in tree.body if predicate(n)]
    assert len(hits) == 1, f"expected exactly one matching top-level node in {module.__name__}, got {len(hits)}"
    return hits[0]


def _abandon_window_as_written(*, slots: int, inflight: int, timeout: int) -> int:
    """Evaluate the module's OWN source for `_worst_case_queue_drain_seconds` and the
    `RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS` assignment under a fake `settings`, without
    reloading the module (a reload would hand every importer a stale function object).
    This is how the derivation itself gets exercised: the constant is computed once at
    import from whatever the env held, so `recon.RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS`
    alone cannot tell `max(3600, drain + 600)` apart from a hard-coded `11400`."""
    fn = _module_node(
        recon, lambda n: isinstance(n, ast.FunctionDef) and n.name == "_worst_case_queue_drain_seconds"
    )
    assign = _module_node(
        recon,
        lambda n: isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS" for t in n.targets),
    )
    ns = {
        "settings": SimpleNamespace(
            MAX_CONCURRENT_AGENT_RUNS=slots,
            MAX_GLOBAL_INFLIGHT_REPORTS=inflight,
            RESEARCH_PIPELINE_TIMEOUT_SECONDS=timeout,
        ),
        "math": math,
    }
    exec(compile(ast.Module(body=[fn, assign], type_ignores=[]), inspect.getfile(recon), "exec"), ns)
    return ns["RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS"]


def test_the_abandon_window_is_computed_from_the_caps_not_hard_coded():
    """Proved necessary by hand: with the assignment replaced by the literal `11400`, every
    other test in this section stayed green — the grid tests exercise the FUNCTION, and the
    constant they compare against was computed from the same defaults. Here the source of the
    assignment is evaluated under caps the defaults never produce, so a literal, or an
    assignment that stopped calling the formula, cannot match."""
    assert _abandon_window_as_written(slots=1, inflight=150, timeout=600) == 149 * 600 + 600
    assert _abandon_window_as_written(slots=8, inflight=8, timeout=600) == 3600, "the 1-hour floor"
    assert _abandon_window_as_written(slots=8, inflight=150, timeout=600) == 11400
    # And the LIVE constant is what that same source yields under the caps in force at import —
    # true under any env override, so this cannot go vacuous when the defaults are retuned.
    slots, inflight, timeout = _IMPORT_TIME_CAPS
    assert (
        _abandon_window_as_written(slots=slots, inflight=inflight, timeout=timeout)
        == recon.RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS
    ), "the live constant does not match its own source evaluated under the import-time caps"


def test_the_refund_windows_are_strictly_ordered():
    """timeout < stuck < abandoned. config.py keeps the pipeline ceiling "STRICTLY below" the
    900 s stuck threshold so a live worker's `wait_for` always refunds first with a clean
    error; and a never-started row must outlast a started one before it is called orphaned."""
    assert settings.RESEARCH_PIPELINE_TIMEOUT_SECONDS < recon.RECON_STUCK_THRESHOLD_SECONDS
    assert recon.RECON_STUCK_THRESHOLD_SECONDS < recon.RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS


# ── 2. `refund_did_not_happen` — the three-shape contract ────────────────────────────────

def test_a_transport_fault_is_a_leak():
    """`refund_ledgered` returns None STRICTLY for a failed round trip. The CAS is already
    spent by then, so this is the case the sweep can never retry — it must page."""
    assert refund_did_not_happen(None) is True


@pytest.mark.parametrize("outcome", _OWED_OUTCOMES)
def test_every_owed_outcome_is_a_leak(outcome):
    """Parametrised over LITERALS: `no_credits_row` had no assertion anywhere, so dropping it
    from the tuple was invisible. `capped_to_zero` is the month-boundary trap; `no_matching_debit`
    the ref_id mismatch. All three mean the user is owed and the one-shot guard is burned."""
    assert refund_did_not_happen({"outcome": outcome, "refunded": 0, "spendable": 140}) is True


def test_the_owed_set_is_exactly_these_three():
    """Both directions. Shrinking it silences an alert; widening it to `already_refunded` turns
    every benign replay into a page — the alert fatigue the predicate's docstring warns is how
    the loud one ends up ignored."""
    assert set(REFUND_FAILURE_OUTCOMES) == set(_OWED_OUTCOMES)


def test_the_settled_set_is_exactly_these_four():
    """The ALLOW-list the predicate actually reads (2026-09-10). Parametrised over literals for
    the same reason as the owed set: widening it to a new name would let that name fall open
    again, and this is the line that stops it."""
    assert set(REFUND_SETTLED_OUTCOMES) == set(_BENIGN_OUTCOMES)


def test_owed_and_settled_partition_the_vocabulary():
    """No outcome may be both, and none may be neither — the second half is what the deny-list
    could not promise."""
    assert not set(REFUND_SETTLED_OUTCOMES) & set(REFUND_FAILURE_OUTCOMES)
    assert set(REFUND_SETTLED_OUTCOMES) | set(REFUND_FAILURE_OUTCOMES) == set(_RPC_OUTCOMES)


@pytest.mark.parametrize(
    "payload",
    [
        {"outcome": "refunded", "refunded": 20, "spendable": 140},
        # The caps resolved to zero — a SUCCESS by the tuple's own comment, mirroring
        # `revoke_purchased_credits` reporting `reclaimed: 0`. Not the same as `capped_to_zero`.
        {"outcome": "refunded", "refunded": 0, "spendable": 140},
        # The envelope `refund_ledgered` synthesises for a pre-142 INTEGER answer.
        {"outcome": "refunded", "refunded": None, "spendable": 140},
    ],
)
def test_a_refund_that_worked_is_not_a_leak(payload):
    assert refund_did_not_happen(payload) is False


@pytest.mark.parametrize("outcome", ["already_refunded", "guest", "invalid"])
def test_a_benign_no_op_is_not_a_leak(outcome):
    """Deliberate: a replayed settlement (`already_refunded`) and the RPC's own guards are
    INFO at the source, and escalating them here would trade a silent-money bug for a noisy
    one. If policy changes, the tuple and this test move together."""
    assert refund_did_not_happen({"outcome": outcome, "refunded": 0}) is False


@pytest.mark.parametrize("legacy", [0, 20, 140])
def test_the_pre_142_bare_integer_is_treated_as_it_happened(legacy):
    """The deploy-window contract: code ships BEFORE migration 142, and until it lands the RPC
    answers a bare `spendable`. Reporting a false leak on every refund for that window would
    bury the real ones, so the old shape means what the old contract forced it to mean."""
    assert refund_did_not_happen(legacy) is False


@pytest.mark.parametrize("garbage", ["", "refunded", [], [{"outcome": "no_credits_row"}], 3.5, True, object()])
def test_the_predicate_never_raises_on_a_shape_it_was_not_promised(garbage):
    """Both endpoint call sites evaluate this INSIDE an error path that has already burned the
    CAS. A predicate that raises there (an `.get` on a list, a `KeyError` on a string) would
    escape past the REFUND LEAK line entirely — the alert would not fire AND the request
    would 500. Whatever the answer, it must be a bool. (Non-dict, non-None shapes take the
    documented "pre-142 integer" clause; dict shapes are pinned individually below.)"""
    assert refund_did_not_happen(garbage) in (True, False)


_REFUND_FN_DEF = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?refund_credits\s*\(")


def _rpc_outcome_vocabulary() -> set[str]:
    """Every outcome literal in the BODY of the latest migration that defines `refund_credits`.
    The body, not the `COMMENT ON FUNCTION`: 142's comment lists six outcomes and omits
    `capped_to_zero`, so the comment would have hidden exactly the one this file is about."""
    mig_dir = Path(inspect.getfile(recon)).resolve().parents[2] / "database" / "migrations"
    defining = [p for p in sorted(mig_dir.glob("[0-9][0-9][0-9]_*.sql")) if _REFUND_FN_DEF.search(p.read_text())]
    assert defining, "no migration defines refund_credits — this vocabulary guard has gone vacuous"
    text = defining[-1].read_text()
    start = _REFUND_FN_DEF.search(text).start()
    body = text[start : text.index("\n$$;", start)]
    return set(re.findall(r"'outcome',\s*'([a-z_]+)'", body)) | set(
        re.findall(r"v_outcome\s*:=\s*'([a-z_]+)'", body)
    )


def test_the_rpc_vocabulary_is_fully_classified():
    """The predicate is an ALLOW-list now, so an outcome the RPC learns to emit tomorrow falls
    CLOSED (alerts) rather than open — but it should still be filed deliberately, not left to
    page on every occurrence. This makes that a build-time decision: a new name in the SQL must
    be classified as owed or settled before it can ship."""
    vocab = _rpc_outcome_vocabulary()
    assert {"refunded", "no_matching_debit", "capped_to_zero"} <= vocab, (
        "the parser is not reading the function body — the guard below would pass vacuously"
    )
    unclassified = vocab - set(REFUND_FAILURE_OUTCOMES) - set(_BENIGN_OUTCOMES)
    assert not unclassified, (
        f"refund_credits can answer {sorted(unclassified)} and refund_did_not_happen treats an "
        "unknown outcome as a successful refund — add each to REFUND_FAILURE_OUTCOMES (owed) or "
        "to _BENIGN_OUTCOMES in this file (not owed)"
    )
    assert set(REFUND_FAILURE_OUTCOMES) <= vocab, "an owed outcome the RPC can never emit is dead code"


@pytest.mark.parametrize(
    "payload",
    [
        {},                                        # a non-dict RPC body, after `payload = {}`
        {"outcome": None},                         # the key present, the value absent
        {"refunded": 0, "spendable": 140},         # the envelope minus its verdict
        {"outcome": "brand_new_outcome"},          # a name the RPC learns to emit tomorrow
        {"outcome": ""},
        {"outcome": "REFUNDED"},                   # case matters — the RPC emits lowercase
    ],
)
def test_an_unproven_envelope_is_a_leak(payload):
    """🔴 Was a strict xfail. The predicate was a DENY-list, so every one of these read as
    "it happened": `refund_ledgered` returns exactly `{}` when the RPC answers with a non-dict
    body, and by then the one-shot CAS is burned — the user silently lost 20 credits and the
    REFUND LEAK alert never fired. Fail toward alerting: a false page costs a human one look,
    a missed one is permanent."""
    assert refund_did_not_happen(payload) is True


# ── 3. `_outcome_name` — what the REFUND LEAK line says happened ─────────────────────────

def test_a_transport_fault_is_named_rpc_failed():
    assert research_endpoint._outcome_name(None) == "rpc_failed"


@pytest.mark.parametrize("outcome", _RPC_OUTCOMES)
def test_a_dict_outcome_is_named_verbatim(outcome):
    """The alert must name the RPC's own word for what happened — that word is what the manual
    correction is keyed on (`capped_to_zero` = check the month boundary, `no_matching_debit` =
    check the ref_id). A constant here fires the alert with the wrong diagnosis."""
    assert research_endpoint._outcome_name({"outcome": outcome, "refunded": 0}) == outcome


def test_a_dict_without_an_outcome_is_named_unknown():
    assert research_endpoint._outcome_name({}) == "unknown"


@pytest.mark.parametrize("legacy", [0, 140])
def test_a_bare_integer_is_named_legacy_int(legacy):
    assert research_endpoint._outcome_name(legacy) == "legacy_int"


@pytest.mark.parametrize("garbage", ["", [], [None], 3.5, True, object()])
def test_the_label_helper_never_raises_and_always_yields_a_string(garbage):
    """It is an argument to `logger.error(...)` on the leak line. If it raised, the alert it
    exists to label would be the thing that did not fire. Any shape in, a `str` out."""
    label = research_endpoint._outcome_name(garbage)
    assert isinstance(label, str) and label


def _function_node(module, name: str) -> ast.AST:
    tree = ast.parse(Path(inspect.getfile(module)).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {module.__name__}")


def test_the_endpoint_and_the_sweep_spell_the_transport_fault_the_same_way():
    """Three sites, one grep. The sweep (`claim_and_mark_failed`) labels a None outcome inline
    rather than through the endpoint helper, so the two spellings can drift apart with nothing
    failing — and `grep rpc_failed` would then find two of the three leak lines. Bounded to
    the ARGUMENTS of the sweep's own REFUND LEAK `logger.error(...)` — not every string
    constant in the function, where a docstring or a dead assignment would also satisfy it.
    Passing the result through `_outcome_name` instead is an accepted spelling."""
    sweep = _function_node(recon, "claim_and_mark_failed")
    leak_calls = [n for n in ast.walk(sweep) if _is_leak_error_call(n)]
    assert leak_calls, "claim_and_mark_failed has no REFUND LEAK logger.error — the sweep site is gone"
    expected = research_endpoint._outcome_name(None)
    for call in leak_calls:
        arg_nodes = [n for a in call.args[1:] for n in ast.walk(a)]
        spelled = {n.value for n in arg_nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        via_helper = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_outcome_name"
            for n in arg_nodes
        )
        assert expected in spelled or via_helper, (
            f"claim_and_mark_failed's REFUND LEAK line (line {call.lineno}) no longer labels a None "
            f"outcome {expected!r} the way _outcome_name(None) does — the leak lines are no longer one grep"
        )


# ── 4. Both endpoint leak lines are gated by the predicate and named by the helper ──────
#
# Sections 2 and 3 prove the helpers are correct. This proves they are REACHED. The comment at
# `research.py` above the first site records the regression: "checking only `is None` missed
# the no-op entirely, which is the case migration 142 exists to surface". A correct predicate
# that one of the two sites stops calling is precisely that bug, back.

def _is_leak_error_call(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "error"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.startswith("REFUND LEAK")
    )


def _endpoint_leak_sites():
    """Every REFUND LEAK `logger.error(...)` in research.py, paired with the innermost `if`
    whose DIRECT body holds it (None when it is not the body of an `if` at all)."""
    tree = ast.parse(Path(inspect.getfile(research_endpoint)).read_text())
    all_calls = [n for n in ast.walk(tree) if _is_leak_error_call(n)]
    gated = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        for stmt in node.body:
            call = getattr(stmt, "value", None)
            if _is_leak_error_call(call):
                gated[id(call)] = node
    sites = [(gated.get(id(call)), call) for call in all_calls]
    # Intrinsic, not a sibling test: an empty list makes every "no offenders" assertion below
    # pass for free, so the helper itself refuses to return one.
    assert len(sites) >= 2, (
        "fewer than two REFUND LEAK logger.error(...) sites in research.py — the insert-failed and "
        "delete-in-flight paths each log one; if they moved or were reworded, point this file at them"
    )
    return sites


def test_there_are_endpoint_side_refund_leak_sites():
    """Anti-vacuity for the guards below: the insert-failed path and the delete-in-flight
    path each log a leak. If those lines move or are reworded, point this file at them."""
    assert len(_endpoint_leak_sites()) >= 2


def test_the_endpoint_gates_on_the_credit_service_predicate_not_a_local_one():
    """The gate test below matches `refund_did_not_happen` by NAME in the AST. A local
    `def refund_did_not_happen(x): return x is None` in research.py would satisfy it while
    reintroducing the exact regression the comment above site 1 records. Identity on the
    module attribute closes that: the name the endpoint calls must BE the credit_service one."""
    assert research_endpoint.refund_did_not_happen is credit_service.refund_did_not_happen
    assert research_endpoint.refund_did_not_happen is refund_did_not_happen


def test_every_endpoint_leak_line_is_gated_by_the_shared_predicate():
    offenders = []
    for if_node, call in _endpoint_leak_sites():
        test = getattr(if_node, "test", None)
        ok = isinstance(test, ast.Call) and isinstance(test.func, ast.Name) and test.func.id == "refund_did_not_happen"
        if not ok:
            offenders.append(call.lineno)
    assert not offenders, (
        f"REFUND LEAK line(s) at research.py:{offenders} are not the body of "
        "`if refund_did_not_happen(...)` — a site that checks only `is None` (or nothing) "
        "misses the migration-142 no-op outcomes and lets the user silently lose 20 credits"
    )


def test_every_endpoint_leak_line_names_the_outcome_through_the_helper():
    offenders = []
    for _if_node, call in _endpoint_leak_sites():
        named = {a.func.id for a in call.args if isinstance(a, ast.Call) and isinstance(a.func, ast.Name)}
        if "_outcome_name" not in named:
            offenders.append(call.lineno)
    assert not offenders, (
        f"REFUND LEAK line(s) at research.py:{offenders} do not pass `_outcome_name(...)` — "
        "the alert fires without saying which outcome stranded the charge"
    )


def _sole_name_arg(call: ast.Call) -> str | None:
    if len(call.args) == 1 and isinstance(call.args[0], ast.Name):
        return call.args[0].id
    return None


def test_the_gate_and_the_label_inspect_the_same_refund_result():
    """`if refund_did_not_happen(refunded): logger.error(..., _outcome_name(claimed))` passes
    both wiring tests above and fires the alert with the wrong diagnosis — the label helper
    is only meaningful when it is handed the SAME `refund_ledgered` result the gate judged.
    Both must be a bare variable, and the same one."""
    offenders = []
    for if_node, call in _endpoint_leak_sites():
        gate_var = _sole_name_arg(if_node.test) if if_node is not None and isinstance(if_node.test, ast.Call) else None
        label_calls = [
            a for a in call.args if isinstance(a, ast.Call) and isinstance(a.func, ast.Name) and a.func.id == "_outcome_name"
        ]
        label_vars = {_sole_name_arg(c) for c in label_calls}
        if gate_var is None or label_vars != {gate_var}:
            offenders.append((call.lineno, gate_var, sorted(map(str, label_vars))))
    assert not offenders, (
        f"REFUND LEAK site(s) {offenders} judge one variable and label another — "
        "`refund_did_not_happen(X)` and `_outcome_name(X)` must share X"
    )
