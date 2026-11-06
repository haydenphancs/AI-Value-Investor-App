"""iOS source-scan guards for live earnings results (owner request 2026-10-29).

There is no XCTest target, so the iOS half of the feature is pinned here, the way every other
screen's contract is:
  * the wire: `EarningsLiveResponse` and its nested DTOs mirror the backend models key for
    key; `AlertDTO` and `TrackingFeedResponse` carry the new feed keys;
  * the route: `APIEndpoint.getEarningsLive` is the backend's path, signed-in only;
  * the honesty rule: LIVE is drawn from the SERVER's status, never from date math on the
    device (`LiveNewsHeader`: nothing live on screen, no "Live");
  * the poll: it stops when the server stops asking, and its starter cancels before re-arming
    (two pollers would double the traffic);
  * the formatting defects the card shipped with ("$-0.25", "$0.0B") stay fixed.
Every scan is comment-stripped first, so a doc comment naming a removed token cannot satisfy
or fail it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas.tracking import (
    AlertResponse,
    EarningsLiveFilingResponse,
    EarningsLiveMetricResponse,
    EarningsLiveQuarterResponse,
    EarningsLiveResponse,
    EarningsLiveYearAgoResponse,
    TrackingFeedResponse,
)

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _code(rel: str) -> str:
    path = _IOS / rel
    assert path.exists(), f"missing: {path}"
    src = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _struct_block(src: str, name: str) -> str:
    start = src.index(f"struct {name}")
    return src[start: src.index("\n}", start)]


def _bracketed(src: str, marker: str, opener: str = "{", closer: str = "}") -> str:
    """What the first `opener` after `marker` encloses, found by counting, so a nested type's body
    and a sibling's never leak into the scan."""
    start = src.index(opener, src.index(marker))
    depth = 0
    for at in range(start, len(src)):
        if src[at] == opener:
            depth += 1
        elif src[at] == closer:
            depth -= 1
            if depth == 0:
                return src[start + 1: at]
    raise AssertionError(f"no closing {closer} after {marker}")


def _coding_keys(src: str, name: str) -> set:
    block = _struct_block(src, name)
    keys = set(re.findall(r'case\s+\w+\s*=\s*"(\w+)"', block))
    enum_at = block.find("enum CodingKeys")
    if enum_at == -1:
        return keys
    for line in re.findall(r"case\s+([\w,\s]+)\n", block[enum_at:]):
        if "=" in line:
            continue
        keys |= {t.strip() for t in line.split(",") if t.strip()}
    return keys


_MODELS = "Models/EarningsLiveModels.swift"


@pytest.mark.parametrize("swift_name,model", [
    ("EarningsLiveResponse", EarningsLiveResponse),
    ("EarningsLiveMetricDTO", EarningsLiveMetricResponse),
    ("EarningsLiveQuarterDTO", EarningsLiveQuarterResponse),
    ("EarningsLiveYearAgoDTO", EarningsLiveYearAgoResponse),
    ("EarningsLiveFilingDTO", EarningsLiveFilingResponse),
])
def test_the_live_dtos_mirror_the_backend_models_key_for_key(swift_name, model):
    ios = _coding_keys(_code(_MODELS), swift_name)
    backend = set(model.model_fields)
    assert ios == backend, (
        f"{swift_name} vs {model.__name__}: iOS-only {sorted(ios - backend)}, "
        f"backend-only {sorted(backend - ios)}"
    )


def test_the_alert_dto_decodes_every_live_card_key():
    ios = _coding_keys(_code("Models/TrackingModels.swift"), "AlertDTO")
    live_keys = {
        "earnings_status", "report_date", "eps_actual", "revenue_actual", "eps_surprise_pct",
        "revenue_surprise_pct", "eps_basis", "results_preliminary", "results_checked_at",
    }
    assert live_keys <= set(AlertResponse.model_fields), "backend dropped a live key"
    assert live_keys <= ios, f"AlertDTO is missing {sorted(live_keys - ios)}"


def test_the_feed_response_decodes_the_recent_earnings_list():
    ios = _coding_keys(_code("Models/TrackingModels.swift"), "TrackingFeedResponse")
    assert ios == set(TrackingFeedResponse.model_fields)
    vm = _code("ViewModels/TrackingViewModel.swift")
    assert "feed.recentEarningsAlerts" in vm, "the previous day's cards are decoded and dropped"


def test_the_endpoint_is_the_backend_route_and_sign_in_only():
    src = _code("Core/Services/APIEndpoint.swift")
    assert 'return "/api/v1/tracking/earnings/\\(ticker)/live"' in src
    assert '["report_date": reportDate]' in src
    policy = src[src.index("var authPolicy: AuthPolicy"):]
    # The arm that holds the Tracking feed itself: from its `case` line to its `return`.
    at = policy.index(".getTrackingAssets")
    arm_start = policy.rfind("case ", 0, at)
    arm = policy[arm_start: policy.index("return ", at)]
    tail = policy[policy.index("return ", at):].split("\n", 1)[0]
    assert ".getEarningsLive" in arm, "the live endpoint is not in the Tracking feed's arm"
    assert tail.strip() == "return .signInRequired", tail


def test_live_is_drawn_from_the_server_status_only():
    models = _code("Models/TrackingModels.swift")
    body = models[models.index("var isLiveEarnings: Bool"):]
    body = body[: body.index("\n    }")]
    assert "data.status == .live" in body
    for token in ("Date()", "Calendar", "isMarketActive", "hour"):
        assert token not in body, f"LIVE is inferred on the device again ({token})"
    card = _code("Views/Molecules/AlertCardView.swift")
    assert "leadingBadge: alert.isLiveEarnings ? .live : nil" in card
    assert "ActivityRow(" in card


def test_the_status_decoder_falls_back_to_upcoming():
    """An older backend sends no status; that is exactly the card this build used to draw."""
    src = _code(_MODELS)
    assert 'EarningsLiveStatus(rawValue: (wire ?? "").lowercased()) ?? .upcoming' in src
    assert 'case notScheduled = "none"' in src, "a case named `none` collides with Optional"


def test_the_sheet_poll_stops_when_the_server_stops_asking():
    vm = _code("ViewModels/EarningsLiveViewModel.swift")
    start = vm[vm.index("func start()"): vm.index("func stop()")]
    assert start.index("pollTask?.cancel()") < start.index("pollTask = Task"), (
        "re-arming leaves the previous poller running"
    )
    assert "guard let seconds = self.live?.pollSeconds, seconds > 0 else { break }" in start
    assert "minimumPollSeconds" in start
    card = _code("Views/Organisms/EarningsLiveResultsCard.swift")
    for hook in (".onAppear { viewModel.start() }", ".onDisappear { viewModel.stop() }",
                 "willResignActiveNotification", "didBecomeActiveNotification"):
        assert hook in card, f"the live card lost its lifecycle hook: {hook}"


def test_the_sheet_hosts_the_live_card_without_changing_its_own_signature():
    detail = _code("Views/Screens/AlertDetailView.swift")
    assert "EarningsLiveResultsCard(ticker: data.ticker, reportDate: data.reportDate," in detail
    assert "var onOpen: (AlertDestination) -> Void" in detail


def test_the_push_offers_live_results_first_and_the_cover_opens_them():
    dest = _code("Models/AlertDestination.swift")
    assert "case earningsLive(ticker: String, reportDate: String?)" in dest
    assert 'label: "See live results"' in dest
    first = dest.index('label: "See live results"')
    assert first < dest.index('label: "Open \\(symbol)"'), "live results must lead"
    cover = _code("Views/Modifiers/AlertDestinationCover.swift")
    assert "EarningsLiveScreen(ticker: ticker, reportDate: reportDate)" in cover


def test_the_consensus_formatting_defects_stay_fixed():
    models = _code("Models/TrackingModels.swift")
    assert 'String(format: "EPS Est: $%.2f"' not in models, "prints a loss as $-0.25"
    assert 'String(format: "Rev Est: $%.1fB"' not in models, "prints $23.5M as $0.0B"
    fmt = _code(_MODELS)
    eps = fmt[fmt.index("static func eps(_ value: Double) -> String"):]
    eps = eps[: eps.index("\n    }")]
    assert 'let sign = value < 0 ? "-" : ""' in eps, "the EPS sign goes after the $ again"
    assert 'String(format: "$%.2f", abs(value))' in eps
    assert 'String(format: "$%.1fM", v / 1_000_000)' in fmt


def test_a_figure_the_server_gave_no_verdict_is_never_coloured():
    """A figure with no surprise beside its consensus is one the server refused to judge: a
    filing's print that covers the consensus, or a consensus under a cent. Comparing the raw
    values would paint a verdict the card, the sheet and the push cannot share: the card only
    has the report-wide preliminary flag, so the rule reads the server's answer and no source.
    A surprise of 0.0 IS an answer, so a beat that rounds to 0.00 stays a beat."""
    block = _struct_block(_code(_MODELS), "EarningsMetricOutcome")
    body = block[block.index("var result: EarningsQuarterResult"):block.index("var color: Color")]
    rule = "if surprisePct == nil { return .noEstimate }"
    assert body.index("guard !isGAAP, let estimate else") < body.index(rule)
    assert body.index(rule) < body.index("actual > estimate")
    assert "surprisePct == 0" not in body
    assert "isPreliminary" not in body, "the card and the sheet pass this flag differently"


@pytest.mark.parametrize("rel,scope,outcome,passed", [
    (_MODELS, "extension EarningsLiveResponse", "var epsOutcome", "eps.surprisePct"),
    (_MODELS, "extension EarningsLiveResponse", "var revenueOutcome", "revenue.surprisePct"),
    ("Models/TrackingModels.swift", "struct EarningsData", "var epsOutcome", "epsSurprisePct"),
    ("Models/TrackingModels.swift", "struct EarningsData", "var revenueOutcome",
     "revenueSurprisePct"),
])
def test_each_outcome_is_handed_its_own_surprise(rel, scope, outcome, passed):
    """`EarningsMetricOutcome.result` reads the surprise as the server's answer, so a nil here turns
    every figure of that surface into a grey "In", with nothing else to show it."""
    body = _bracketed(_bracketed(_code(rel), scope), outcome)
    assert re.search(rf"\bsurprisePct:\s*{re.escape(passed)}\s*[,)]", body), body


@pytest.mark.parametrize("label,dto_field", [
    ("epsSurprisePct", "epsSurprisePct"),
    ("revenueSurprisePct", "revenueSurprisePct"),
])
def test_the_feed_hands_the_card_each_surprise_it_decoded(label, dto_field):
    models = _code("Models/TrackingModels.swift")
    call = _bracketed(models, ".earnings(AppAlert.EarningsData", "(", ")")
    assert re.search(rf"\b{label}:\s*{dto_field}\s*,", call), call


def test_the_colour_word_and_arrow_all_come_from_the_one_verdict():
    block = _struct_block(_code(_MODELS), "EarningsMetricOutcome")
    for name, end in (("var color: Color", "var word: String?"),
                      ("var word: String?", "var surpriseText: String?"),
                      ("var arrowSymbol: String?", None)):
        start = block.index(name)
        body = block[start: block.index(end) if end else len(block)]
        assert "switch result {" in body, f"{name} works its own verdict out"
        assert "actual" not in body and "estimate" not in body, f"{name} compares raw figures"


def test_the_poll_keeps_running_for_a_live_card_after_twenty_et():
    vm = _code("ViewModels/TrackingViewModel.swift")
    timer = vm[vm.index("func startPriceRefreshTimer()"): vm.index("func stopPriceRefreshTimer()")]
    assert "self.alerts.contains(where: \\.isLiveEarnings)" in timer
    assert "hasLiveEarnings || MarketHoursUtil.shouldRefreshPrices(" in timer


def test_a_result_row_is_hidden_only_for_the_same_report_on_screen():
    """Ticker AND report date: last quarter's result row stays while next quarter's card is
    up, and a row without a report date (sent before the route carried it) is never hidden."""
    tab = _code("Views/Organisms/AlertsTabContent.swift")
    body = tab[tab.index("private func duplicatesLiveResultCard("):]
    body = body[: body.index("\n    }")]
    assert "AlertDestination.isEarningsResult(item.kind)" in body
    assert 'let date = item.route["report_date"], !date.isEmpty else { return false }' in body
    assert 'upcomingReports.contains("\\(ticker)|\\(date)")' in body
    section = tab[tab.index("private func activitySection()"):]
    assert "&& !duplicatesLiveResultCard($0, upcomingReports: upcomingReports)" in section
    upcoming = tab[tab.index("private func duplicatesUpcomingCard("):]
    upcoming = upcoming[: upcoming.index("\n    }")]
    assert "isEarningsResult" not in upcoming, "the upcoming rule must stay ticker-scoped"


def test_the_outlook_is_never_cut_on_the_card():
    """The server prints the company's sentence whole, up to the 400 characters a quote may be, and
    refuses any quote it would have to shorten. A line limit here would undo that, and a limit on
    the card's body reaches every text inside it."""
    src = _code("Views/Organisms/EarningsLiveResultsCard.swift")
    block = _bracketed(src, "func guidanceBlock")
    assert "Text(guidance)" in block
    assert ".fixedSize(horizontal: false, vertical: true)" in block
    for cut in (".lineLimit", ".truncationMode", ".prefix("):
        assert cut not in block
        assert cut not in _bracketed(src, "var body: some View")


def test_every_file_using_published_imports_combine():
    """`SWIFT_UPCOMING_FEATURE_MEMBER_IMPORT_VISIBILITY` is on, so a file cannot lean on the
    Combine that `import SwiftUI` drags in: `@Published` / `ObservableObject` need their own
    `import Combine`. EarningsLiveViewModel shipped without it, written without a compiler."""
    project = _IOS.parent / "ios.xcodeproj/project.pbxproj"
    assert "SWIFT_UPCOMING_FEATURE_MEMBER_IMPORT_VISIBILITY = YES" in project.read_text(
        encoding="utf-8"
    ), "the build setting that makes this guard necessary is gone: retire the guard with it"
    missing = []
    scanned = 0
    for path in sorted(_IOS.parent.rglob("*.swift")):
        code = _code(str(path.relative_to(_IOS))) if _IOS in path.parents else None
        if code is None:
            src = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
            code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
        if not re.search(r"@Published\b|\bObservableObject\b", code):
            continue
        scanned += 1
        if not re.search(r"^\s*(?:@\w+\s+)?import\s+Combine\b", code, re.M):
            missing.append(str(path.relative_to(_IOS.parent)))
    assert scanned >= 40, f"the scan found only {scanned} files: it is not reading the tree"
    assert not missing, f"these files use @Published/ObservableObject with no import Combine: {missing}"
