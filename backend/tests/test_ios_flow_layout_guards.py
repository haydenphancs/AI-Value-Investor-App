"""`FlowLayout` (the wrapping chip atom) must never lay a child out at its one-line width.

Until 2026-09-24 it measured AND placed every subview with `sizeThatFits(.unspecified)`, so a
child wider than the row — a long chip label, a sentence, any chip at an AX text size — was
laid out on one line and ran past its column. The Trillion-Dollar Club review found it
(finding `ios-flowlayout-text-overflow`: "Listed since … — not on a 13F yet" was 279pt in a
260pt card); the club views moved their sentences out of the flow, but seven files still use
the atom (onboarding, investor preferences, news related tickers, the club card, row, sheet
and detail).

The fix, pinned here:

  1. `.unspecified` is used ONLY to read the ideal width, and only inside
     `min(<subview>.sizeThatFits(.unspecified).width, maxWidth)` — so a too-wide child is
     offered the row's width instead of its one-line width.
  2. The only proposal the atom ever constructs is that capped width with a FREE height
     (`ProposedViewSize(width: <capped>, height: nil)`), so the child wraps and grows taller.
     No `.unspecified` aliases (`width: nil`, `.infinity`, `ProposedViewSize(size)`).
  3. Every other `sizeThatFits` call measures with that capped proposal, and every child is
     PLACED with the proposal it was measured with.
  4. `sizeThatFits` and `placeSubviews` both read ONE arrangement (`arrange(proposal: proposal,
     …)`), and neither measures a subview itself or re-derives rows from `bounds` — so the size
     the parent reserves is the geometry that gets drawn.

Comment-stripped and brace-bounded to `struct FlowLayout` with the scanner the Trillion Club
parity test uses (nested block comments, string literals lifted out). Each rule has an in-memory
MUTATION test beside it, including the exact pre-fix implementation, a fix hidden inside a
comment, and a fix moved to a different struct in the same file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

import pytest

from test_trillion_club_schema_parity import match_brace, scan_swift, type_body

REPO = Path(__file__).resolve().parents[2]
FLOW = REPO / "frontend" / "ios" / "ios" / "Views" / "Atoms" / "FlowLayout.swift"

# The ideal-width probe: the one legal use of `.unspecified`.
_PROBE = re.compile(r"min\(\s*\w+\.sizeThatFits\(\s*\.unspecified\s*\)\.width\s*,\s*maxWidth\s*\)")
_PROBE_TOKEN = "__IDEAL_WIDTH_PROBE__"
# The one legal proposal: the capped ideal width, height free.
_CAPPED = re.compile(
    rf"ProposedViewSize\(\s*width:\s*{_PROBE_TOKEN}\s*,\s*height:\s*nil\s*\)"
)
_CAPPED_BINDING = re.compile(
    rf"\blet\s+(\w+)\s*=\s*ProposedViewSize\(\s*width:\s*{_PROBE_TOKEN}\s*,\s*height:\s*nil\s*\)"
)
# Spellings of "no proposal" that are `.unspecified` under another name.
_UNSPECIFIED_ALIASES = {
    r"\.unspecified\b": "`.unspecified` outside the capped ideal-width probe",
    r"\bwidth:\s*nil\b": "a `width: nil` proposal (that IS `.unspecified`)",
    r"ProposedViewSize\.infinity\b": "`ProposedViewSize.infinity` (the one-line width again)",
    r"\.replacingUnspecifiedDimensions\(": "`replacingUnspecifiedDimensions` (a fixed stand-in width)",
}


def _src() -> str:
    assert FLOW.is_file(), f"{FLOW} moved — update this guard, do not delete it"
    return FLOW.read_text(encoding="utf-8")


def _call_args(code: str, open_paren: int) -> str:
    """`open_paren` indexes a `(`; returns the text up to its matching `)`."""
    depth = 0
    for i in range(open_paren, len(code)):
        if code[i] == "(":
            depth += 1
        elif code[i] == ")":
            depth -= 1
            if depth == 0:
                return code[open_paren + 1:i]
    raise ValueError("unbalanced parentheses")


def _func_body(body: str, name: str) -> Optional[str]:
    """The body of the ONE `func <name>` in `body`, or None if there is not exactly one."""
    matches = list(re.finditer(rf"\bfunc\s+{name}\s*\(", body))
    if len(matches) != 1:
        return None
    brace = body.index("{", matches[0].end())
    return body[brace + 1:match_brace(body, brace + 1)]


def flow_layout_violations(src: str) -> List[str]:
    code = scan_swift(src)[0]
    body = type_body(code, "FlowLayout")
    out: List[str] = []

    # 1. `.unspecified` only as the capped ideal-width probe.
    if not _PROBE.search(body):
        out.append("the capped ideal-width probe `min(subview.sizeThatFits(.unspecified).width, "
                   "maxWidth)` is gone")
    rest = _PROBE.sub(_PROBE_TOKEN, body)
    for pattern, what in _UNSPECIFIED_ALIASES.items():
        if re.search(pattern, rest):
            out.append(f"FlowLayout uses {what}")

    # 2. The only proposal it constructs is the capped width with a free height.
    for m in re.finditer(r"\bProposedViewSize\s*\(", rest):
        if not _CAPPED.match(rest, m.start()):
            args = _call_args(rest, m.end() - 1)
            out.append(f"a proposal other than the capped ideal width: ProposedViewSize({args.strip()})")
    capped = set(_CAPPED_BINDING.findall(rest))
    if not capped:
        out.append("no `let <p> = ProposedViewSize(width: <capped ideal width>, height: nil)` binding")

    # 3a. Every measurement (other than the probe) uses a capped proposal.
    for m in re.finditer(r"\.sizeThatFits\s*\(", rest):
        arg = _call_args(rest, m.end() - 1).strip()
        if arg not in capped:
            out.append(f"a subview is measured with `{arg}`, not the capped proposal")
    # 3b. ...and is recorded, then placed, with that same proposal.
    items = list(re.finditer(r"\bItem\s*\(", rest))
    if not items:
        out.append("the arrangement no longer records each child's proposal (`Item(…)`)")
    for m in items:
        arg = re.search(r"\bproposal:\s*(\w+)\s*$", _call_args(rest, m.end() - 1))
        if not arg or arg.group(1) not in capped:
            out.append("a child's recorded proposal is not the capped one it was measured with")
    places = list(re.finditer(r"\.place\s*\(", rest))
    if not places:
        out.append("FlowLayout no longer places its subviews")
    for m in places:
        if not re.search(r"\bproposal:\s*\w+\.proposal\s*$", _call_args(rest, m.end() - 1)):
            out.append("a subview is placed with a proposal other than the one it was measured with")

    # 4. One arrangement feeds both the reported size and the placement.
    if _func_body(rest, "arrange") is None:
        out.append("there is no single shared `func arrange` deciding the geometry")
    for fn in ("sizeThatFits", "placeSubviews"):
        fn_body = _func_body(rest, fn)
        if fn_body is None:
            out.append(f"expected exactly one `func {fn}` in FlowLayout")
            continue
        if not re.search(r"\barrange\(\s*proposal:\s*proposal\s*,", fn_body):
            out.append(f"`{fn}` does not read the shared `arrange(proposal: proposal, …)`")
        if ".sizeThatFits(" in fn_body:
            out.append(f"`{fn}` measures a subview itself instead of reading the arrangement")
        if re.search(r"\bbounds\.(?:maxX|width|size)\b", fn_body):
            out.append(f"`{fn}` re-derives rows from `bounds` instead of the proposal it was sized for")
    return out


def test_flow_layout_never_lays_a_child_out_at_its_one_line_width():
    assert flow_layout_violations(_src()) == []


# ── Mutation tests: re-introduce each defect into the real source; the guard must fire ──


def _replace_once(src: str, old: str, new: str) -> str:
    assert src.count(old) == 1, f"mutation anchor not found exactly once: {old!r}"
    return src.replace(old, new)


# The implementation that shipped the bug, verbatim (2026-09-24 and earlier).
_PRE_FIX = """
import SwiftUI

struct FlowLayout: Layout {
    var spacing: CGFloat = AppSpacing.sm
    var lineSpacing: CGFloat = AppSpacing.sm

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) -> CGSize {
        let maxWidth = proposal.width ?? .greatestFiniteMagnitude
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var widestRow: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > maxWidth {
                y += rowHeight + lineSpacing
                x = 0
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
            widestRow = max(widestRow, x - spacing)
        }
        return CGSize(width: min(widestRow, maxWidth), height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX {
                y += rowHeight + lineSpacing
                x = bounds.minX
                rowHeight = 0
            }
            subview.place(
                at: CGPoint(x: x, y: y),
                anchor: .topLeading,
                proposal: ProposedViewSize(size)
            )
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
"""

_PROBE_SRC = "min(subview.sizeThatFits(.unspecified).width, maxWidth)"
_MEASURE_SRC = "let size = subview.sizeThatFits(childProposal)"
_PLACE_SRC = "proposal: item.proposal"
_PLACE_ARRANGE_SRC = "let arrangement = arrange(proposal: proposal, subviews: subviews)"
_SIZE_ARRANGE_SRC = "arrange(proposal: proposal, subviews: subviews).size"
_ITEM_SRC = "Item(origin: CGPoint(x: x, y: y), proposal: childProposal)"


def test_the_pre_fix_implementation_fails():
    v = flow_layout_violations(_PRE_FIX)
    assert any("probe" in s for s in v), v
    assert any("ProposedViewSize(size)" in s for s in v), v
    assert any("placeSubviews" in s and "bounds" in s for s in v), v


@pytest.mark.parametrize("old,new,expect", [
    # Measured at the one-line width again.
    (_MEASURE_SRC, "let size = subview.sizeThatFits(.unspecified)", "`.unspecified`"),
    # The cap removed: a too-wide child is offered its one-line width.
    (_PROBE_SRC, "subview.sizeThatFits(.unspecified).width", "probe"),
    # The cap spelled as an unspecified alias.
    (_MEASURE_SRC, "let size = subview.sizeThatFits(ProposedViewSize(width: nil, height: nil))", "width: nil"),
    (_MEASURE_SRC, "let size = subview.sizeThatFits(.infinity)", "measured with `.infinity`"),
    # Placed with something other than what was measured.
    (_PLACE_SRC, "proposal: .unspecified", "`.unspecified`"),
    (_PLACE_SRC, "proposal: ProposedViewSize(width: nil, height: nil)", "width: nil"),
    (_ITEM_SRC, "Item(origin: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))", "ProposedViewSize(size)"),
    # placeSubviews re-derives its rows from bounds; sizeThatFits from nothing.
    (_PLACE_ARRANGE_SRC,
     "let arrangement = arrange(proposal: ProposedViewSize(width: bounds.width, height: nil), subviews: subviews)",
     "shared `arrange"),
    (_SIZE_ARRANGE_SRC, "arrange(proposal: .unspecified, subviews: subviews).size", "`.unspecified`"),
])
def test_each_regression_fires(old, new, expect):
    v = flow_layout_violations(_replace_once(_src(), old, new))
    assert any(expect in s for s in v), v


def test_a_fix_left_only_in_a_comment_does_not_count():
    """Comment-stripped: the probe written in a comment next to the reverted code is not a probe."""
    mutated = _replace_once(_src(), _PROBE_SRC,
                            f"subview.sizeThatFits(.unspecified).width /* {_PROBE_SRC} */")
    v = flow_layout_violations(mutated)
    assert any("probe" in s for s in v), v


def test_a_fix_moved_to_another_struct_does_not_count():
    """Brace-bounded: the correct body under a different name does not cover `FlowLayout`."""
    fixed_elsewhere = _src().replace("struct FlowLayout: Layout", "struct FlowLayoutV2: Layout")
    v = flow_layout_violations(fixed_elsewhere + _PRE_FIX.replace("import SwiftUI", ""))
    assert v, "the guard read FlowLayoutV2's body as FlowLayout's"


def test_the_guard_reads_the_real_file():
    """Not vacuous: the anchors the mutations use exist in the shipped source."""
    code = scan_swift(_src())[0]
    for anchor in (_PROBE_SRC, _MEASURE_SRC, _PLACE_SRC, _PLACE_ARRANGE_SRC, _SIZE_ARRANGE_SRC, _ITEM_SRC):
        assert code.count(anchor) == 1, anchor
