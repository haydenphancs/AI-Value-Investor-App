"""The Holdings "Sort By" comparator and the membership write sequencer, EXECUTED (2026-10-31).

Owner report: Tracking › Holdings › Manage Tickers "doesn't change the ticker card at all". The
drag saved, but the cards were sorted by Name and only "Date Added" read the stored order. The
fix (owner decisions 2026-10-31):

* a Custom option backed by the group's stored order, which a drag on the active group switches
  to;
* "Date Added" reads the real add date (`watchlist_items.added_at`), not the stored order;
* "Name" displays as "Symbol" (it always compared tickers), Change as "Change %";
* unknown values — a "—" price, a missing cap, a missing date — sort LAST in both directions
  (they used to sort as 0, so unpriced rows led Price ↑ and missing caps led Market Cap ↓);
* every option starts in its natural direction (largest / newest first for the numbers).

And `MembershipWriteSequencer`, the bookkeeping that lets `PortfolioStore` send a portfolio's
whole-list PUTs one at a time and apply only the newest answer.

Both files are Foundation-only, so they are piped into `xcrun swift -` and run here, rather
than grepped (precedent: test_ios_whale_13f_quarter_label.py). Skips when xcrun is unavailable.
⚠️ This compiles Swift: in a session on the owner's Mac it needs the build slot (CLAUDE.md
"Machine safety"), like every executed-Swift test.

Category 1 (pure) — no network, no Supabase.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_SORT = _IOS / "Core" / "Utilities" / "HoldingsSort.swift"
_SEQUENCER = _IOS / "Core" / "Services" / "MembershipWriteSequencer.swift"

_HARNESS = r"""
var failures = 0
func check<T: Equatable>(_ name: String, _ got: T, _ want: T) {
    if got == want { print("ok|\(name)") } else { failures += 1; print("FAIL|\(name)|got \(got)|want \(want)") }
}

func date(_ s: String) -> Date? { HoldingsSort.parseAddedAt(s) }

// ── the comparator ───────────────────────────────────────────────────────────
// A: everything known.  B: smallest price, negative move, biggest cap, newest, first in order.
// C: everything unknown.  D: biggest price, no cap.  E: non-finite numbers (unknown), bad date.
let keys: [HoldingsSort.Key] = [
    .init(ticker: "a", price: 10, changePercent: 1.0, marketCap: 100,
          addedAt: date("2026-01-01T00:00:00.000Z"), position: 2),
    .init(ticker: "B", price: 5, changePercent: -2.0, marketCap: 300,
          addedAt: date("2026-03-01T00:00:00.000Z"), position: 0),
    .init(ticker: "C", price: nil, changePercent: nil, marketCap: nil, addedAt: nil, position: nil),
    .init(ticker: "D", price: 20, changePercent: 0.5, marketCap: nil,
          addedAt: date("2026-02-01T00:00:00.000Z"), position: 1),
    .init(ticker: "E", price: .nan, changePercent: .infinity, marketCap: 200,
          addedAt: date("not a date"), position: 3),
]

func order(_ option: AssetSortOption, _ ascending: Bool, _ input: [HoldingsSort.Key] = keys) -> String {
    HoldingsSort(option: option, ascending: ascending).ordered(input) { $0 }.map(\.ticker).joined()
}

check("custom|asc", order(.custom, true), "BDAEC")
check("custom|desc_ignores_direction", order(.custom, false), "BDAEC")
check("name|asc", order(.name, true), "ABCDE")
check("name|desc", order(.name, false), "EDCBA")
check("price|asc_unknown_last", order(.price, true), "BADCE")
check("price|desc_unknown_last", order(.price, false), "DABCE")
check("change|asc_unknown_last", order(.change, true), "BDACE")
check("change|desc_unknown_last", order(.change, false), "ADBCE")
check("cap|asc_unknown_last", order(.marketCap, true), "AEBCD")
check("cap|desc_unknown_last", order(.marketCap, false), "BEACD")
check("date|asc_oldest_first", order(.dateAdded, true), "ADBCE")
check("date|desc_newest_first", order(.dateAdded, false), "BDACE")

// Ties settle by ticker A→Z in BOTH directions (the two screens start from different orders).
let ties: [HoldingsSort.Key] = [
    .init(ticker: "Z", price: 1, changePercent: 0, marketCap: 5, addedAt: date("2026-01-01T00:00:00Z"), position: nil),
    .init(ticker: "M", price: 1, changePercent: 0, marketCap: 5, addedAt: date("2026-01-01T00:00:00Z"), position: nil),
    .init(ticker: "A", price: nil, changePercent: nil, marketCap: nil, addedAt: nil, position: nil),
    .init(ticker: "Q", price: nil, changePercent: nil, marketCap: nil, addedAt: nil, position: nil),
]
for option in [AssetSortOption.price, .change, .marketCap, .dateAdded] {
    check("ties|\(option.rawValue)|asc", order(option, true, ties), "MZAQ")
    check("ties|\(option.rawValue)|desc", order(option, false, ties), "MZAQ")
}
check("ties|custom_all_unknown", order(.custom, true, ties), "AMQZ")

// Signed zero and a negative cap are still KNOWN values (never folded into "unknown").
let zeros: [HoldingsSort.Key] = [
    .init(ticker: "N", price: -0.0, changePercent: -0.0, marketCap: -1, addedAt: nil, position: nil),
    .init(ticker: "U", price: nil, changePercent: nil, marketCap: nil, addedAt: nil, position: nil),
    .init(ticker: "P", price: 0.01, changePercent: 0.01, marketCap: 1, addedAt: nil, position: nil),
]
check("zero|price_asc", order(.price, true, zeros), "NPU")
check("zero|change_desc", order(.change, false, zeros), "PNU")
// A non-positive cap is UNKNOWN (the Updates tabs send None for it): N joins U at the end.
check("zero|cap_asc_non_positive_is_unknown", order(.marketCap, true, zeros), "PNU")

// One row, no rows, a repeated ticker: no trap, stable output.
check("edge|single", order(.price, false, [keys[0]]), "A")
check("edge|empty", order(.price, false, []), "")
check("edge|duplicate_ticker", order(.name, true, [keys[1], keys[1]]), "BB")

// ── the option table ─────────────────────────────────────────────────────────
check("menu|custom_first", AssetSortOption.allCases.first!, .custom)
check("menu|six_options", AssetSortOption.allCases.count, 6)
check("label|name_is_symbol", AssetSortOption.name.displayName, "Symbol")
check("label|change_is_percent", AssetSortOption.change.displayName, "Change %")
check("raw|name_unchanged", AssetSortOption.name.rawValue, "Name")
check("raw|date_unchanged", AssetSortOption.dateAdded.rawValue, "Date Added")
check("natural|custom", AssetSortOption.custom.naturalAscending, true)
check("natural|name", AssetSortOption.name.naturalAscending, true)
check("natural|price", AssetSortOption.price.naturalAscending, false)
check("natural|change", AssetSortOption.change.naturalAscending, false)
check("natural|cap", AssetSortOption.marketCap.naturalAscending, false)
check("natural|date", AssetSortOption.dateAdded.naturalAscending, false)
check("dir|custom_none", AssetSortOption.custom.directionLabels == nil, true)
check("dir|name", AssetSortOption.name.directionLabels.map { "\($0.ascending)/\($0.descending)" } ?? "", "A → Z/Z → A")
check("dir|price", AssetSortOption.price.directionLabels.map { "\($0.ascending)/\($0.descending)" } ?? "", "Low → High/High → Low")
check("dir|date", AssetSortOption.dateAdded.directionLabels.map { "\($0.ascending)/\($0.descending)" } ?? "", "Oldest First/Newest First")

// ── the stored preference survives (legacy raw values, unknown values) ───────
func loaded(option: String?, ascending: Bool?) -> String {
    let suite = "holdings-sort-test-\(option ?? "nil")-\(ascending.map(String.init) ?? "nil")"
    let defaults = UserDefaults(suiteName: suite)!
    defaults.removePersistentDomain(forName: suite)
    if let option { defaults.set(option, forKey: "TrackingView.sortOption") }
    if let ascending { defaults.set(ascending, forKey: "TrackingView.sortAscending") }
    let sort = HoldingsSort.load(from: defaults)
    defaults.removePersistentDomain(forName: suite)
    return "\(sort.option.rawValue)/\(sort.ascending)"
}
check("load|nothing_stored", loaded(option: nil, ascending: nil), "Name/true")
check("load|legacy_name", loaded(option: "Name", ascending: false), "Name/false")
check("load|legacy_date_added", loaded(option: "Date Added", ascending: true), "Date Added/true")
check("load|custom", loaded(option: "Custom", ascending: nil), "Custom/true")
check("load|unknown_option_keeps_direction", loaded(option: "Bogus", ascending: false), "Name/false")
do {
    let suite = "holdings-sort-test-roundtrip"
    let defaults = UserDefaults(suiteName: suite)!
    defaults.removePersistentDomain(forName: suite)
    HoldingsSort(option: .custom, ascending: true).save(to: defaults)
    check("save|roundtrip", HoldingsSort.load(from: defaults), HoldingsSort(option: .custom, ascending: true))
    defaults.removePersistentDomain(forName: suite)
}

// ── the add date off the wire ────────────────────────────────────────────────
let whole = date("2026-09-14T12:34:56Z")
check("date|whole_seconds_parses", whole != nil, true)
check("date|millis", date("2026-09-14T12:34:56.789Z").map { ($0.timeIntervalSince(whole!) * 1000).rounded() }, 789)
check("date|offset_is_the_same_instant", date("2026-09-14T07:34:56-05:00"), whole)
check("date|trimmed", date("  2026-09-14T12:34:56Z\n"), whole)
check("date|garbage", date("yesterday") == nil, true)
check("date|empty", date("") == nil, true)
check("date|blank", date("   ") == nil, true)
check("date|nil", HoldingsSort.parseAddedAt(nil) == nil, true)
check("date|date_only_is_not_a_timestamp", date("2026-09-14") == nil, true)

// ── the sequencer ────────────────────────────────────────────────────────────
var seq = MembershipWriteSequencer()
let r0 = seq.revisionSnapshot()
let t1 = seq.enqueueMembership("p1")
check("seq|first_is_newest", seq.isNewest("p1", ticket: t1), true)
check("seq|pending_after_enqueue", seq.hasPending("p1"), true)
check("seq|revision_moved", seq.revisionSnapshot() != r0, true)
let t2 = seq.enqueueMembership("p1")
check("seq|stale_response_not_newest", seq.isNewest("p1", ticket: t1), false)
check("seq|second_is_newest", seq.isNewest("p1", ticket: t2), true)
check("seq|portfolios_are_independent", seq.isNewest("p2", ticket: t1), false)
let other = seq.enqueueMembership("p2")
check("seq|other_portfolio_newest", seq.isNewest("p2", ticket: other), true)
check("seq|other_portfolio_leaves_p1", seq.isNewest("p1", ticket: t2), true)
let revBeforeHoldings = seq.revision("p1")
seq.enqueueOther("p1")   // a holdings write joins the chain without a ticket
check("seq|holdings_keeps_newest_membership", seq.isNewest("p1", ticket: t2), true)
check("seq|holdings_moves_revision", seq.revision("p1") != revBeforeHoldings, true)
let revBeforeFinish = seq.revision("p1")
seq.finish("p1")
// A write that settles while a load is out must move the revision, or the load (which saw it
// pending at request time and nothing pending at answer time) publishes the older list.
check("seq|finish_moves_revision", seq.revision("p1") != revBeforeFinish, true)
seq.finish("p1")
check("seq|still_pending_after_two_of_three", seq.hasPending("p1"), true)
seq.finish("p1")
check("seq|drained", seq.hasPending("p1"), false)
seq.finish("p1")   // an extra finish never goes negative or traps
check("seq|extra_finish_harmless", seq.hasPending("p1"), false)
let t3 = seq.enqueueMembership("p1")
check("seq|tickets_keep_increasing", t3 > t2, true)
seq.forget("p1")
check("seq|forget_drops_newest", seq.isNewest("p1", ticket: t3), false)
check("seq|forget_drops_pending", seq.hasPending("p1"), false)
seq.reset()
check("seq|reset_drops_everything", seq.hasPending("p2") || seq.isNewest("p2", ticket: other), false)
check("seq|reset_revisions", seq.revisionSnapshot().isEmpty, true)

print("DONE|\(failures)")
"""


def _run_swift() -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    for path in (_SORT, _SEQUENCER):
        assert path.exists(), f"{path} is missing — every executed case is vacuous"
    source = (
        _SORT.read_text(encoding="utf-8") + "\n"
        + _SEQUENCER.read_text(encoding="utf-8") + "\n"
        + _HARNESS
    )
    try:
        proc = subprocess.run(
            ["xcrun", "swift", "-"], input=source,
            text=True, capture_output=True, timeout=240, env={**os.environ},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail(
            "the Swift harness did not run to completion — usually a file stopped compiling "
            "standalone (a SwiftUI import or an app type will do it).\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr[-4000:]}"
        )
    return proc.stdout


@pytest.fixture(scope="module")
def swift_output() -> str:
    return _run_swift()


def test_every_sort_and_sequencer_case(swift_output: str):
    failures = [line for line in swift_output.splitlines() if line.startswith("FAIL|")]
    assert not failures, "mismatches:\n  " + "\n  ".join(line.replace("|", "  ") for line in failures)


def test_the_harness_actually_asserted_something(swift_output: str):
    """Anti-vacuity: a harness that compiled but ran few checks would pass the test above."""
    oks = [line.split("|", 1)[1] for line in swift_output.splitlines() if line.startswith("ok|")]
    assert len(oks) >= 70, f"only {len(oks)} checks ran — the harness was truncated"
    for required in ("custom|desc_ignores_direction", "price|desc_unknown_last",
                     "cap|desc_unknown_last", "date|desc_newest_first", "load|legacy_date_added",
                     "date|offset_is_the_same_instant", "seq|stale_response_not_newest",
                     "seq|holdings_keeps_newest_membership", "seq|reset_revisions",
                     "seq|finish_moves_revision", "zero|cap_asc_non_positive_is_unknown"):
        assert required in oks, f"the {required} case did not run"
