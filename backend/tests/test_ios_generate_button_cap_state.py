"""At the per-user report cap the Generate button says so (E2, TestFlight 2026-08-27).

`ContentView` fed `viewModel.isAtConcurrencyCap` into the button's `isLoading`, so the
fifth attempt met a disabled spinner with nothing explaining why; the VM's own "You can
run up to 4 analyses at once" message sat behind a `.disabled` button and could not fire.
Now the cap is its own state: a plain disabled button under an `InlineRetryNotice` that
names the count and links to the Reports tab. Pinned from the Swift source (no XCTest
target), comment-stripped and brace-bound, plus a parity pin between the client's
mirrored cap and the server's.
"""

import re
from pathlib import Path

import pytest

from app.config import Settings

# The CODE default, not the env-resolved instance: Railway may override the cap per
# environment, and the client cannot follow an env var — it mirrors what ships in
# config.py (review finding, 2026-09-19).
_SERVER_CAP_DEFAULT = Settings.model_fields["MAX_CONCURRENT_REPORTS_PER_USER"].default

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_BUTTON = _IOS / "Views/Molecules/GenerateAnalysisButton.swift"
_SECTION = _IOS / "Views/Organisms/GenerateAnalysisSection.swift"
_CONTENT = _IOS / "ContentView.swift"
_VM = _IOS / "ViewModels/ResearchViewModel.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _decl_block(src: str, prefix: str) -> str:
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this scan has drifted"
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail(f"unbalanced braces after {prefix!r}")


def test_the_client_cap_mirrors_the_server_cap():
    vm = _code(_VM)
    m = re.search(r"let maxConcurrentGenerations\s*=\s*(\d+)", vm)
    assert m, "ResearchViewModel.maxConcurrentGenerations is gone"
    assert int(m.group(1)) == _SERVER_CAP_DEFAULT, (
        "the client's mirrored cap drifted from the MAX_CONCURRENT_REPORTS_PER_USER code "
        "default — the button would disable early (or late, and every fifth tap would 409)"
    )


def test_the_client_cap_survives_a_poll_timeout_and_follows_the_server():
    """A run that outlives the 300 s client poll is still running — and still counted —
    on the server. The poll-timeout arm used to drop its slot, re-enabling Generate under
    a live report (the next tap met the server's 409). The slot is now kept and released
    by the list poll once the row is terminal or gone."""
    vm = _code(_VM)
    body = _decl_block(vm, "func generateAnalysis()")
    arm = body[body.index("if case .timeout = appError, startedId != nil {"):]
    arm = arm[:arm.index("} else if case .timeout = appError {")]
    assert "self.inFlightReportIds.insert(id)" in arm, "the poll-timeout arm no longer keeps its slot"
    load = _decl_block(vm, "func loadReports()")
    assert "releaseFinishedSlots(against: backendReports)" in load
    rel = _decl_block(vm, "private func releaseFinishedSlots(")
    assert '$0.status == "pending" || $0.status == "processing"' in rel
    assert "inFlightReportIds.subtracting(stillRunning)" in rel
    assert "inFlightReportIds.remove(id)" in rel


def test_the_button_has_no_spinner_and_disables_on_the_cap():
    body = _decl_block(_code(_BUTTON), "struct GenerateAnalysisButton")
    assert "ProgressView" not in body, "the at-cap spinner is back"
    assert "isLoading" not in body, "`isLoading` is back — the cap is not a loading state"
    assert re.search(r"var isAtCap:\s*Bool\s*=\s*false", body)
    assert ".disabled(!isInteractive)" in body
    assert re.search(r"var isInteractive:\s*Bool\s*\{\s*isEnabled && !isAtCap\s*\}", body)
    # The label is the same at the cap — it is the disabled surface that says "no".
    assert 'Text("Generate Analysis")' in body


def test_the_section_explains_the_cap_under_the_button():
    body = _decl_block(_code(_SECTION), "struct GenerateAnalysisSection")
    block = _decl_block(body, "if isAtCap")
    assert "InlineRetryNotice(" in block, "the at-cap notice is gone"
    assert "wait for one to finish" in block
    assert "activeCount" in block, "the notice must name the live count"
    assert "iconColor: AppColors.textMuted" in block, "not a failure — no caution icon"
    assert 'retryTitle: "View progress"' in block
    assert "onRetry: onViewProgress" in block
    # The notice is the ONLY reader of isAtCap in the section besides the button.
    assert body.count("isAtCap") >= 3


def test_content_view_passes_the_cap_as_its_own_state():
    src = _code(_CONTENT)
    at = src.index("GenerateAnalysisSection(")
    call = src[at:src.index(")", src.index("onViewProgress", at)) + 1]
    assert "isAtCap: viewModel.isAtConcurrencyCap" in call
    assert "activeCount: viewModel.activeGenerationCount" in call
    assert "isEnabled: viewModel.canStartNewGeneration" in call
    assert "onViewProgress: { viewModel.selectedTab = .reports }" in call
    assert "isLoading" not in call, "the cap is fed into a loading flag again"
