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


# ── Both writers must carry the magnitude backstop, and both must GATE it ───────────────

def _stripped(rel: str) -> str:
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / rel).read_text()
    return "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )


def test_both_whale_writers_apply_the_magnitude_backstop():
    """`hydrate_whales` IMPORTED `is_implausible_share_flow` and never called it.

    `test_both_whale_writers_use_the_shared_rule` above checks
    `restate_prev_shares_for_split` usage, so the missing backstop sat in the same file,
    one import line away from a guard that was already being source-scanned, for as long
    as it existed. This writer populates `whale_trades`, which feeds user alerts.
    """
    for rel in ("app/services/whale_service.py", "scripts/hydrate_whales.py"):
        code = _stripped(rel)
        assert "is_implausible_share_flow(" in code, (
            f"{rel} imports the magnitude backstop but never calls it — an unnameable "
            f"corporate action goes straight into a written trade"
        )


def test_neither_writer_suppresses_on_magnitude_alone():
    """The backstop must be GATED on an unclassified corporate action.

    Ungated, `|change| >= 50% of shares held` drops 10.1% of real 13F holder rows —
    measured over 1,000 rows across 10 mega-caps — because the threshold is calibrated for
    an AGGREGATE across every holder, not for one position. Doubling or halving a single
    holding is an ordinary conviction trade.
    """
    for rel in ("app/services/whale_service.py", "scripts/hydrate_whales.py",
                "app/services/holders_service.py"):
        code = _stripped(rel)
        i = code.index("is_implausible_share_flow(")
        # the condition must mention the gate on the same statement
        stmt_start = code.rfind("if ", 0, i)
        stmt = code[stmt_start:i]
        assert ("unclassified" in stmt), (
            f"{rel} suppresses on magnitude alone — that deletes ~10% of genuine "
            f"institutional trades, including the highest-conviction ones"
        )


# ── The corporate-action lookup fan-out is bounded on the REQUEST path ───────────────


def test_the_two_split_lookup_paths_share_one_cap():
    """`hydrate_whales` and `whale_service` must not carry independent copies.

    This module's own docstring records what happens otherwise: three copies of the 13F
    diff drifted apart, and the annual-return formula did it a second time. The cap lives
    in `_whale_common` for that reason.
    """
    import app.services.whale_service as ws
    import app.services._whale_common as wc

    assert ws._MAX_SPLIT_LOOKUPS is wc.MAX_SPLIT_LOOKUPS
    assert isinstance(wc.MAX_SPLIT_LOOKUPS, int) and wc.MAX_SPLIT_LOOKUPS > 0


def test_the_request_path_caps_its_suspect_fan_out():
    """`_process_13f_path` runs on a USER REQUEST and each suspect costs two FMP calls.

    A derived split reads `historical-price-eod/full` AND `/non-split-adjusted`, so an
    entire restated book — a fund that changed custodian, where every position looks like
    a share multiple — would fan out unbounded FMP calls inside one request and burn the
    rate-limit budget the rest of the app shares. The nightly hydrator has always capped
    this; the request path, added later, did not.

    Source-scanned rather than driven end-to-end because the caller needs a full 13F
    fixture pair; the invariant here is that the slice EXISTS in the function that builds
    the suspect list, which a fixture staying under the cap could never show.

    ⚠️ Bound to `_process_13f_path` — the function that actually calls
    `_suspicious_split_tickers` — NOT to `_diff_quarters`, which consumes the result.
    Scanning the whole module would pass on the hydrator's copy; scanning the wrong
    method passes on nothing at all (this test was written against `_diff_quarters`
    first, and failed for exactly that reason).
    """
    import inspect
    import re

    import app.services.whale_service as ws

    src = inspect.getsource(ws.WhaleService._process_13f_path)
    # Comments carry every token this greps for — strip them, or the assertion passes on
    # prose after the code is reverted (`.claude/rules/testing.md` §3).
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    )

    assert re.search(r"suspects\s*=\s*suspects\[:\s*_MAX_SPLIT_LOOKUPS\s*\]", code), (
        "the suspect list must be truncated to _MAX_SPLIT_LOOKUPS where it is built"
    )
    assert re.search(r"len\(suspects\)\s*>\s*_MAX_SPLIT_LOOKUPS", code), (
        "the overflow must be detected so it can be logged, not silently truncated"
    )


def test_the_hydrator_still_caps_too():
    """Mutation guard against 'consolidating' the cap onto one path only."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "scripts" / "hydrate_whales.py"
    code = "\n".join(
        line for line in src.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )

    assert re.search(r"suspects\s*=\s*suspects\[:\s*_MAX_SPLIT_LOOKUPS\s*\]", code)
    assert "_MAX_SPLIT_LOOKUPS = 25" not in code, (
        "the hydrator must import the shared cap, not redeclare it"
    )


# ── The Holders tab knows its basis; it must NOT use the ambiguity resolver ──────────


def _holder_row(*, last, curr, price=90.0, name="Fund"):
    """One FMP `institutional-ownership` analytics row.

    `sharesNumber` and `lastSharesNumber` arrive in the SAME row, and
    `changeInSharesNumber` is FMP's own raw `curr - last`.
    """
    return {
        "investorName": name,
        "sharesNumber": curr,
        "lastSharesNumber": last,
        "changeInSharesNumber": curr - last,
        "marketValue": curr * price,
    }


def _activities(rows, split_ratio=1.0, unclassified=False):
    from app.services.holders_service import HoldersService

    svc = HoldersService.__new__(HoldersService)
    out = svc._build_institutional_activities(
        rows, split_ratio=split_ratio, unclassified_action=unclassified
    )
    return [(r.institution_name, round(r.change_in_millions, 1),
             round(r.change_percent, 2)) for r in out]


@pytest.mark.parametrize("pre_split_last, curr, want_millions, want_pct, why", [
    # 10:1. The holder's restated prior book is last * 10.
    (100_000,   540_000, -41.4, -46.00, "sold 46% — was rendered +$39.6M BOUGHT / +440%"),
    (100_000,   800_000, -18.0, -20.00, "sold 20% — was DELETED by SPLIT_SUPPRESS"),
    (100_000, 1_240_000,  21.6,  24.00, "bought 24% — was DELETED by SPLIT_SUPPRESS"),
    (100_000,   500_000, -45.0, -50.00, "sold half"),
    (100_000, 2_000_000,  90.0, 100.00, "doubled"),
])
def test_a_split_quarter_reports_the_real_move_not_the_multiplication(
    pre_split_last, curr, want_millions, want_pct, why
):
    """`restate_prev_shares_for_split` resolves an ambiguity this caller does not have.

    It weighs H0 "the feed is already split adjusted" against H1 "the feed is raw". That
    is a real question for `_compute_quarter_flow` and both whale writers, which diff two
    INDEPENDENTLY FETCHED snapshots. Here both counts come from one row, and FMP's own
    `changeInSharesNumberPercentage` of +901.88% on BlackRock's KLAC row across the 10:1
    proves it does not restate — H0 is structurally impossible.

    Routing through it anyway produced a WRONG-SIGN fabrication. `ratio_obs` for a holder
    that sold 46% is 5.4: outside 15% of 10, and just under the 5.5 midpoint, so the
    helper took its "did not move toward the split" branch and returned the RAW pre-split
    count. Anything worse than -45% on a 10:1 landed there; anything past ±15% in the
    other direction tripped SPLIT_SUPPRESS and was deleted from the top-15 outright.
    """
    got = _activities([_holder_row(last=pre_split_last, curr=curr)], split_ratio=10.0)

    assert got == [("Fund", want_millions, want_pct)], why


def test_the_klac_incident_row():
    """The row that started this: BlackRock across KLAC's 10:1 on 2026-06-12.

    Shipped as `+$34,275.0M / +901.88%` when `split_ratio` never reached this builder.
    """
    got = _activities(
        [_holder_row(last=1_000_000, curr=10_236_583, price=900.0, name="BlackRock")],
        split_ratio=10.0,
    )

    assert got[0][0] == "BlackRock"
    assert got[0][1] == pytest.approx(212.9, abs=0.2), "a ~$213M add, not $34.3bn"
    assert got[0][2] == pytest.approx(2.37, abs=0.05), "+2.4%, not +901.88%"


@pytest.mark.parametrize("last, curr, why", [
    (1_310_000, 4_080_000, "Citadel +212% in XOM"),
    (16_100_000, 6_200_000, "UBS -62% in XOM"),
    (10_700_000, 7_100_000, "Barclays -34% in KO"),
])
def test_a_conviction_trade_outside_a_split_quarter_survives(last, curr, why):
    """The 10.1% of real rows the ungated magnitude backstop used to delete."""
    got = _activities([_holder_row(last=last, curr=curr)], split_ratio=1.0)

    assert len(got) == 1, why


def test_the_percentage_always_agrees_with_the_dollar_figure():
    """FMP's percentage is the FALLBACK, not the primary.

    It used to be primary with a `0.0` default, so a row carrying neither spelling
    published a confident "0.00%" beside a real multi-million-dollar change.
    """
    row = _holder_row(last=1_310_000, curr=4_080_000)
    assert "changeInSharesNumberPercentage" not in row

    (_, millions, pct), = _activities([row], split_ratio=1.0)

    assert millions > 0
    assert pct == pytest.approx(211.45, abs=0.05), "computed, not defaulted to 0.0"


def test_a_brand_new_position_still_uses_fmps_percentage():
    """`prev_shares == 0` makes the division undefined — that is what the fallback is for."""
    row = _holder_row(last=0, curr=500_000)
    row["changeInSharesNumberPercentage"] = 100.0

    (_, _, pct), = _activities([row], split_ratio=1.0)

    assert pct == 100.0


# ── "We could not check" is not "there is nothing" ──────────────────────────────────


def _gate_source(fn_name, module):
    import inspect
    src = inspect.getsource(getattr(module, fn_name)) if not isinstance(fn_name, str) else None
    return src


@pytest.mark.parametrize("path, fn", [
    ("app/services/whale_service.py", "_process_13f_path"),
    ("app/services/holders_service.py", "_build_holders"),
])
def test_a_failed_probe_arms_the_backstop_rather_than_clearing_it(path, fn):
    """FAIL CLOSED. The probe and the split ratio come from ONE derivation.

    `has_unclassified_adjustment` and `get_split_rows` are two reads of the same derived
    events, so a timeout / 429 / entitlement error takes out both together. The handlers
    used to answer that with `unclassified_tickers = set()` / `inst_unclassified = False`
    — "we could not check" encoded as "we checked and there is nothing". And that is the
    worst possible moment for it: with the ratio gone there is no restatement either, so
    it is exactly the state in which a split renders as a multi-million-dollar purchase
    and, in the whale path, gets WRITTEN to `whale_trades` and alerted on.

    `_whale_common` already states the trade-off: "a missing bar is recoverable; a
    fabricated multi-million dollar BOUGHT that feeds an alert is not."
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / path).read_text()
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    assert not re.search(r"unclassified_tickers\s*=\s*set\(\)\s*$", code, re.M), (
        f"{path}: the batch handler clears the flags on the failure that causes them"
    )
    assert not re.search(r"inst_unclassified\s*=\s*False", code), (
        f"{path}: the probe handler encodes 'could not check' as 'nothing found'"
    )


@pytest.mark.parametrize("path", [
    "app/services/whale_service.py",
    "scripts/hydrate_whales.py",
])
def test_a_per_ticker_probe_exception_also_fails_closed(path):
    """`gather(return_exceptions=True)` hands back the EXCEPTION OBJECT.

    `if flagged is True` read that as "no corporate action" — a per-ticker version of the
    same bug, and the one that actually fires, since one bad symbol is far likelier than
    a whole batch failing.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / path).read_text()
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    assert re.search(
        r"if flagged is True or isinstance\(flagged, BaseException\)", code
    ), f"{path}: a probe exception must arm the backstop, not clear it"


@pytest.mark.parametrize("path", [
    "app/services/whale_service.py",
    "scripts/hydrate_whales.py",
])
def test_the_gate_is_asked_about_the_diffed_period_not_the_fetch_window(path):
    """`window_for_range` adds a 10-day lead so a split on day one has a prior bar.

    `_split_ratio_in_window` filters the RATIO back to `prev_end < d <= curr_end`; the
    gate must be asked the same question or an unnameable event in the previous quarter's
    last 10 days — ~11% of every diff — arms the backstop for this one.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / path).read_text()
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    assert re.search(
        r"has_unclassified_adjustment\([^)]*effective_from=prev_end,\s*effective_to=curr_end",
        code, re.S,
    ), f"{path}: the gate is asked over the fetch window, not the diffed period"


def test_the_holders_gate_is_narrowed_to_the_data_quarter():
    """Holders fetches FOUR quarters but narrows its split ratio to ONE.

    375 days vs 91: T's WBD spin-off of 2025-07-01 kept the gate True through a full year
    of later quarters, arming the backstop on rows with no corporate action near them.
    """
    import inspect
    import re

    import app.services.holders_service as hs

    code = "\n".join(
        line for line in inspect.getsource(hs.HoldersService._build_holders).splitlines()
        if not line.lstrip().startswith("#")
    )

    assert "effective_window_for_quarter(data_year, data_quarter)" in code
    assert re.search(r"effective_from=eff_from,\s*effective_to=eff_to", code, re.S)
