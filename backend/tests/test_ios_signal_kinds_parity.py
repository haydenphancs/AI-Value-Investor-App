"""App-Exclusive Signals: the backend's card set and the iOS client must agree (home E2).

Adding the CEO Buys card (2026-09-23) touched five places that each hard-coded the three
existing kinds. Any one of them missed fails SILENTLY — no crash, no error:

  * `SignalGroupsDTO` without the field → the card is decoded away, never shown;
  * `mapSignals` without a block → decoded, then dropped;
  * `ExclusiveSignal.drillDownKinds` short of the backend's `_VALID_SIGNAL_KINDS` → the
    leader tap opens the plain ticker screen instead of "who bought";
  * `drillDownKinds` LONGER than the backend's set → a 400 INVALID_INPUT on tap;
  * `SignalDetailModels` without an explicit case → CEO rows worded as congress
    ("No congress members bought GME…").

So each is derived from the backend here rather than listed. `SignalDollarFormat` (the
"$46.8M bought" figure) is EXECUTED via `xcrun swift -` — no XCTest target exists.

Source scans follow `.claude/rules/testing.md` §3: comments stripped, declarations
brace-bounded, and every guard was mutation-tested by hand (see the module's STATUS line).
Category 1 (pure) — no network.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.api.v1.endpoints import home as home_endpoint
from app.schemas.home_dashboard import SignalsGroupResponse
from app.schemas.signals_detail import SignalHolderResponse

BACKEND = Path(__file__).resolve().parents[1]
IOS = BACKEND.parent / "frontend/ios/ios"
DASH_MODELS = IOS / "Models/HomeDashboardModels.swift"
REPO = IOS / "Core/Repositories/HomeRepository.swift"
DETAIL_MODELS = IOS / "Models/SignalDetailModels.swift"
DASH_VIEW = IOS / "Views/Screens/HomeDashboardView.swift"
DOLLARS = IOS / "Core/Utilities/SignalDollarFormat.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        m = re.search(r"\s//", line)
        if m and line[: m.start()].count('"') % 2 == 0:
            line = line[: m.start()]
        out.append(line)
    return "\n".join(out)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} is missing — every assertion below would be vacuous"
    return _strip_comments(path.read_text())


def _block_after(src: str, anchor: str) -> str:
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found"
    open_at = src.index("{", at)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at : i + 1]
    pytest.fail(f"unbalanced braces after `{anchor}`")


def _backend_groups() -> set[str]:
    return set(SignalsGroupResponse.model_fields)


# ── 1. The card set ──────────────────────────────────────────────────────────────────

def test_swift_signal_groups_dto_declares_every_backend_group():
    body = _block_after(_code(DASH_MODELS), "struct SignalGroupsDTO: Decodable")
    fields = dict(re.findall(r"let\s+(\w+)\s*:\s*(\w+\??)", body))
    assert set(fields) == _backend_groups(), (sorted(fields), sorted(_backend_groups()))
    for name, typ in fields.items():
        assert typ == "SignalGroupDTO?", f"`{name}` must stay Optional — a required group crashes the whole dashboard decode"


def test_map_signals_renders_every_backend_group():
    body = _block_after(_code(REPO), "private static func mapSignals(")
    for group in _backend_groups():
        assert re.search(rf"\bdto\.{group}\b", body), f"mapSignals never reads dto.{group} — the card is decoded, then dropped"
        assert f'kind: "{group}"' in body, f"mapSignals has no card of kind {group!r}"


def test_the_ceo_card_chrome():
    body = _block_after(_code(REPO), "private static func mapSignals(")
    ceo = body[body.find("dto.ceo"):]
    for needed in ('title: "CEO Buys"', "AppColors.alertPurple", "SignalDollarFormat.compact("):
        assert needed in ceo, needed
    # A TEXT-role accent: never a *Fill or *Graphic token on an icon glyph.
    assert not re.search(r"accent:\s*AppColors\.\w+(Fill|Graphic)\b", body)


def test_ios_drill_down_kinds_match_the_backends_valid_kinds():
    code = _code(DASH_MODELS)
    m = re.search(r"static let drillDownKinds:\s*Set<String>\s*=\s*\[([^\]]*)\]", code)
    assert m, "ExclusiveSignal.drillDownKinds is gone"
    ios = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert ios == home_endpoint._VALID_SIGNAL_KINDS, (sorted(ios), sorted(home_endpoint._VALID_SIGNAL_KINDS))
    assert ios <= _backend_groups()


def test_the_leader_tap_routes_through_drill_down_kinds():
    body = _block_after(_code(DASH_VIEW), "private func openLeader(")
    assert "ExclusiveSignal.drillDownKinds.contains(kind)" in body
    assert '"whale"' not in body and '"congress"' not in body, "a hand-written kind list is back"


# ── 2. The drill-down display model ─────────────────────────────────────────────────

def test_signal_detail_has_an_explicit_ceo_case_everywhere():
    code = _code(DETAIL_MODELS)
    for anchor in ("func toDisplay(kind: String) -> SignalHolder", "var subtitleLine: String",
                   "var emptyText: String"):
        body = _block_after(code, anchor)
        assert 'case "ceo"' in body, f"{anchor}: no explicit CEO case — it falls into congress wording"
        assert "switch kind" in body, anchor
    to_display = _block_after(code, "func toDisplay(kind: String) -> SignalHolder")
    ceo = to_display[to_display.find('case "ceo"'): to_display.find("default:")]
    assert "whaleId: nil" in ceo, "a CEO is never a registry whale — the row must not be tappable"
    assert "SignalDetailFormat.insiderDate(" in ceo and "SignalDollarFormat.sharesAtPrice(" in ceo


def test_holder_dto_decodes_every_backend_field_including_shares():
    body = _block_after(_code(DETAIL_MODELS), "struct SignalHolderDTO: Decodable")
    keys = _block_after(body, "enum CodingKeys")
    raw = set(re.findall(r'=\s*"(\w+)"', keys))
    bare = set()
    for case_line in re.findall(r"case\s+([^\n]+)", keys):
        for part in case_line.split(","):
            name = part.strip().split("=")[0].strip()
            if name and "=" not in part:
                bare.add(name)
    wire = raw | bare
    assert set(SignalHolderResponse.model_fields) <= wire, sorted(set(SignalHolderResponse.model_fields) - wire)
    assert "shares = try c.decodeIfPresent(Double.self, forKey: .shares)" in body


# ── 3. SignalDollarFormat, executed ────────────────────────────────────────────────

HARNESS = r"""
var failures = 0
func check(_ name: String, _ got: String?, _ expect: String?) {
    if got == expect { print("ok|\(name)") }
    else { failures += 1; print("FAIL|\(name)|got=\(String(describing: got))|expect=\(String(describing: expect))") }
}
typealias F = SignalDollarFormat
check("gme", F.compact(46_770_000), "$46.8M")
check("fox", F.compact(10_270_000), "$10.3M")
check("uber_whole", F.compact(10_000_000), "$10M")
check("fox_uber_distinct", String(F.compact(10_270_000) != F.compact(10_010_000)), "true")
check("floor", F.compact(100_000), "$100K")
check("k_decimal", F.compact(1_234), "$1.2K")
check("under_k", F.compact(512), "$512")
check("fraction", F.compact(0.4), "$0.4")
check("unit_after_rounding_k_to_m", F.compact(999_999), "$1M")
check("unit_after_rounding_m_to_b", F.compact(999_960_000), "$1B")
check("just_below_rounds_within_unit", F.compact(999_940), "$999.9K")
check("promotes_at_the_print_boundary", F.compact(999_960), "$1M")
check("dollars_to_k_boundary_below", F.compact(999.94), "$999.9")
check("dollars_to_k_boundary_at", F.compact(999.96), "$1K")
check("m_to_b_just_below", F.compact(999_940_000), "$999.9M")
check("billion", F.compact(999_730_000), "$999.7M")
check("trillion", F.compact(2_500_000_000_000), "$2.5T")
check("zero", F.compact(0), "—")
check("negative", F.compact(-5_000), "—")
check("nan", F.compact(.nan), "—")
check("inf", F.compact(.infinity), "—")
check("shares_at_price", F.sharesAtPrice(shares: 1_870_800, amount: 46_770_000), "1.9M sh @ $25.00")
check("shares_small", F.sharesAtPrice(shares: 4_000, amount: 100_600), "4K sh @ $25.15")
check("shares_zero", F.sharesAtPrice(shares: 0, amount: 1_000), nil)
check("shares_nil", F.sharesAtPrice(shares: nil, amount: 1_000), nil)
check("amount_nil", F.sharesAtPrice(shares: 10, amount: nil), nil)
check("shares_nan", F.sharesAtPrice(shares: .nan, amount: 1_000), nil)
check("amount_negative", F.sharesAtPrice(shares: 10, amount: -1), nil)
check("tiny_shares_huge_amount", F.sharesAtPrice(shares: 1e-320, amount: 1e300), nil)
print("DONE|\(failures)")
"""


@pytest.fixture(scope="module")
def swift_output() -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    try:
        proc = subprocess.run(["xcrun", "swift", "-"], input=DOLLARS.read_text() + "\n" + HARNESS,
                              text=True, capture_output=True, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail(f"harness did not complete (a SwiftUI import?)\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr[-3000:]}")
    return proc.stdout


def test_dollar_format_is_foundation_only():
    raw = DOLLARS.read_text()
    code = _strip_comments(raw)
    assert "import Foundation" in code and "import SwiftUI" not in code and "import UIKit" not in code
    assert "nonisolated enum SignalDollarFormat" in code
    assert "import SwiftUI" in raw, "the header explaining the rule is gone (anti-vacuity)"


def test_every_dollar_format_case(swift_output: str):
    failures = [l for l in swift_output.splitlines() if l.startswith("FAIL|")]
    assert not failures, "\n  ".join(failures)
    oks = [l for l in swift_output.splitlines() if l.startswith("ok|")]
    assert len(oks) >= 29, f"only {len(oks)} checks ran"


def test_comment_stripping_is_not_vacuous():
    sample = '// dto.ceo\nlet x = 1 // case "ceo"\n'
    assert "dto.ceo" not in _strip_comments(sample) and 'case "ceo"' not in _strip_comments(sample)
    assert "let x = 1" in _strip_comments(sample)
