"""One split-restatement rule, shared by both 13F writers.

There were THREE copies of this test (`whale_service._diff_quarters`,
`hydrate_whales._diff_quarters`, `holders_service._compute_quarter_flow`) and they had
already drifted twice. The two whale copies each carried a distinct live defect:

  * `hydrate_whales` restated on the AMBIGUOUS branch. Its `if` and `elif` bodies were
    character-identical, so its "three-way test" was really two-way — a comment claiming
    parity with `whale_service` sitting over code that did the opposite. Restating on an
    unverified premise is not a small error: `calc_13f_trade_dollars` turns a bad
    `prev_shares` into a WRONG-SIGN trade, so a holder who bought is reported as a
    seller.
  * BOTH whale copies used `ratio_obs >= (1.0 + ratio) / 2.0` unconditionally. That
    classifier only points the right way for a FORWARD split. On a REVERSE split the
    count shrinks, so the correct test flips to `<=` — and an ordinary quarter sitting
    at `ratio_obs ~ 1.0` was being misclassified.

`holders_service` had already fixed the direction bug and added a magnitude backstop.
That is the behaviour encoded in `_whale_common.restate_prev_shares_for_split`.

Pure logic — no network. Run via `python -m pytest` from backend/.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services._whale_common import (
    SPLIT_SUPPRESS,
    calc_13f_trade_dollars,
    is_implausible_share_flow,
    restate_prev_shares_for_split,
)


def _r(prev, curr, ratio):
    out = restate_prev_shares_for_split(prev, curr, ratio)
    return "SUPPRESS" if out is SPLIT_SUPPRESS else out


# ── Forward splits ───────────────────────────────────────────────────────────────────
def test_clean_forward_split_is_restated():
    """1000 → 2000 on a 2:1 with no trading. Restate; the residual is zero."""
    assert _r(1_000, 2_000, 2.0) == 2_000.0


def test_ambiguous_forward_split_is_suppressed():
    """2:1 split AND a concurrent sale — inseparable, so emit nothing.

    Restating here is *sometimes* right, but only when the split genuinely applies to
    the previous quarter. Branch 2 is by construction the branch that has already
    conceded it cannot verify that.
    """
    assert _r(1_000, 1_600, 2.0) == "SUPPRESS"


def test_count_that_did_not_move_keeps_the_raw_diff():
    """Spinoff / ADR-ratio change / an already-adjusted feed mislabelled as a split."""
    assert _r(1_000, 1_050, 2.0) == 1_000


# ── Reverse splits — the direction bug ───────────────────────────────────────────────
def test_reverse_split_normal_quarter_is_not_misclassified():
    """THE REGRESSION. 1:5 reverse (ratio 0.2) → midpoint 0.6. A quiet quarter sits at
    ratio_obs = 1.0, which satisfies the old unconditional `>= 0.6` — so it landed in
    the ambiguous branch, where `hydrate_whales` restated 1,000,000 → 200,000 and booked
    a fabricated +800,000-share BOUGHT out of thin air."""
    assert _r(1_000_000, 1_000_000, 0.2) == 1_000_000, (
        "a quiet quarter on a reverse-split ticker is being misclassified again"
    )


def test_clean_reverse_split_is_restated():
    assert _r(1_000_000, 200_000, 0.2) == pytest.approx(200_000.0)


def test_reverse_split_with_concurrent_flow_is_suppressed():
    """Raw feed, 1:5 reverse, holder then bought. Both implementations previously
    emitted a fabricated SOLD of ~$7.6M here."""
    assert _r(1_000_000, 240_000, 0.2) == "SUPPRESS"


# ── Guards ───────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ratio", [1.0, 0.0, None])
def test_no_ratio_leaves_prev_shares_untouched(ratio):
    assert _r(1_000, 2_000, ratio) == 1_000


@pytest.mark.parametrize("prev,curr", [(0, 500), (500, 0), (-10, 500), (0, 0)])
def test_degenerate_counts_are_left_alone(prev, curr):
    """No usable ratio_obs — never divide by zero, never guess."""
    assert _r(prev, curr, 2.0) == prev


def test_suppress_sentinel_is_distinguishable_from_a_restated_value():
    """`SPLIT_SUPPRESS` must not be confused with a falsy restatement — a caller doing
    `if not restated` would treat a legitimate 0 as suppression and vice versa."""
    assert SPLIT_SUPPRESS is not None
    assert SPLIT_SUPPRESS != 0
    assert restate_prev_shares_for_split(1_000, 1_600, 2.0) is SPLIT_SUPPRESS


# ── The magnitude backstop ───────────────────────────────────────────────────────────
def test_magnitude_guard_catches_a_bad_restatement():
    """Ported from holders_service: a quarterly net change cannot plausibly exceed ~half
    the shares HELD. Catches a bad restatement whichever branch produced it."""
    assert is_implausible_share_flow(450_000, 650_000) is True
    assert is_implausible_share_flow(-450_000, 650_000) is True


def test_magnitude_guard_allows_ordinary_flow():
    assert is_implausible_share_flow(40, 1_600) is False
    assert is_implausible_share_flow(-40, 1_600) is False


def test_magnitude_guard_is_nan_safe():
    """NaN comparisons are all False, so a naive `>=` guard would wave NaN through."""
    nan = float("nan")
    assert is_implausible_share_flow(nan, 1_000) is True
    assert is_implausible_share_flow(float("inf"), 1_000) is True
    assert is_implausible_share_flow(10, 0) is False   # nothing held → no opinion


# ── The consequence the rule exists to prevent ───────────────────────────────────────
def test_a_bad_restatement_would_flip_the_SIGN_not_just_the_size():
    """Why ambiguity must suppress rather than guess.

    Forward 2:1 already applied to both quarters, holder bought 60%. Restating turns a
    genuine PURCHASE into a reported SALE.
    """
    prev_shares, curr_shares = 1_000, 1_600
    curr_value = 100_800.0

    honest, _ = calc_13f_trade_dollars(
        curr_shares=curr_shares, curr_value=curr_value,
        prev_shares=prev_shares, prev_value=100_000.0,
    )
    assert honest == "BOUGHT"

    wrongly_restated, _ = calc_13f_trade_dollars(
        curr_shares=curr_shares, curr_value=curr_value,
        prev_shares=prev_shares * 2.0, prev_value=100_000.0,
    )
    assert wrongly_restated == "SOLD", "premise of this test changed"

    # ...and the shared rule refuses to make that call at all.
    assert restate_prev_shares_for_split(prev_shares, curr_shares, 2.0) is SPLIT_SUPPRESS


# ── Source guard: no local re-implementation ─────────────────────────────────────────
def test_both_whale_writers_use_the_shared_rule():
    import app.services.whale_service as ws
    import scripts.hydrate_whales as hw
    from app.services import _whale_common as wc

    assert ws.restate_prev_shares_for_split is wc.restate_prev_shares_for_split
    assert hw.restate_prev_shares_for_split is wc.restate_prev_shares_for_split


def test_neither_writer_still_computes_the_midpoint_inline():
    """The old test, re-implemented locally, is how these drifted in the first place."""
    for rel in ("app/services/whale_service.py", "scripts/hydrate_whales.py"):
        src = (Path(__file__).resolve().parents[1] / rel).read_text()
        body = "\n".join(
            ln.split("#", 1)[0] for ln in src.splitlines() if ln.split("#", 1)[0].strip()
        )
        assert "(1.0 + ratio) / 2.0" not in body, f"{rel} re-implements the midpoint test"
        assert "ratio_obs" not in body, f"{rel} re-implements the split classifier"


# ── The empty-previous-quarter guard, on BOTH 13F writers ────────────────────────────
def _strip(src: str) -> str:
    import re
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return "\n".join(
        ln.split("#", 1)[0] for ln in src.splitlines() if ln.split("#", 1)[0].strip()
    )


def _fn_body(rel: str, name: str) -> str:
    src = (Path(__file__).resolve().parents[1] / rel).read_text()
    start = src.index(name)
    nxt = src.find("\n    async def ", start + 1)
    end = nxt if nxt != -1 else len(src)
    return _strip(src[start:end])


def test_both_13f_writers_refuse_an_empty_previous_quarter():
    """`prev_raw == []` means either "no prior filing" or "the fetch just failed", and
    the second one makes `_diff_quarters` book EVERY position as New/BOUGHT — a single
    429 reports the whale as having bought its whole AUM, which also clears the $500M
    alert threshold and pushes a fabricated notification.

    `hydrate_whales` guarded this; `whale_service` — the USER-FACING path — did not.
    """
    hyd = _fn_body("scripts/hydrate_whales.py", "async def _process_13f(")
    assert "prev_entry and not prev_raw" in hyd, "hydration guard removed"

    svc = _fn_body("app/services/whale_service.py", "async def _process_13f_path(")
    assert "prev and not prev_raw" in svc, (
        "the serve path will book a whole book as new positions on a transient FMP error"
    )
