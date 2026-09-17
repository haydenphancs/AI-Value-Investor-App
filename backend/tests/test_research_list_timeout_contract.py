"""The research list carries `processing_started_at`, and iOS decodes it.

WHY THIS EXISTS. `ResearchViewModel.applyClientSideTimeoutPass` used to age EVERY
`processing` row from `created_at` at 600 s — the same number as the server's
`RESEARCH_PIPELINE_TIMEOUT_SECONDS`, but the server counts from work START (after the
agent semaphore), so a report queued for two minutes was shown as failed while the
server was still generating it, and Retry charged a second 20 credits. The fix keys the
client on the server's own stamp, which needs the list endpoint to SELECT it, the schema
to CARRY it, and the Swift DTO to DECODE it. Three places; a source scan over each.
"""

import re
from pathlib import Path

from app.schemas.research import ResearchReportListItem

_REPO = Path(__file__).resolve().parents[2]
_ENDPOINT = _REPO / "backend" / "app" / "api" / "v1" / "endpoints" / "research.py"
_DTO = _REPO / "frontend" / "ios" / "ios" / "Core" / "Services" / "TaskPollingManager.swift"
_VM = _REPO / "frontend" / "ios" / "ios" / "ViewModels" / "ResearchViewModel.swift"
_MODEL = _REPO / "frontend" / "ios" / "ios" / "Models" / "ResearchModels.swift"
_API_ENDPOINT = _REPO / "frontend" / "ios" / "ios" / "Core" / "Services" / "APIEndpoint.swift"


def _strip_swift_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", l) for l in src.splitlines())


def test_the_schema_carries_processing_started_at():
    assert "processing_started_at" in ResearchReportListItem.model_fields
    item = ResearchReportListItem(id="r", ticker="AAPL", investor_persona="warren_buffett",
                                  status="processing", created_at="2026-09-12T00:00:00Z")
    assert item.processing_started_at is None, "optional — old rows and queued reports have none"


def test_the_list_endpoint_selects_it():
    src = _ENDPOINT.read_text(encoding="utf-8")
    # The list handler's select string names created_at and processing_started_at together.
    m = re.search(r'current_step, created_at, processing_started_at, completed_at', src)
    assert m, "GET /research/reports no longer selects processing_started_at"


def test_the_ios_list_dto_decodes_it():
    src = _strip_swift_comments(_DTO.read_text(encoding="utf-8"))
    block = re.search(r"struct BackendReportListItem: Sendable \{(.*?)\n\}", src, re.S)
    assert block, "BackendReportListItem not found"
    assert "let processingStartedAt: String?" in block.group(1)
    assert 'case processingStartedAt = "processing_started_at"' in block.group(1)
    # The DTO has a HAND-WRITTEN decoder that assigns every stored property; a field added
    # to the struct but not to it is a compile error — pin the assignment so this test goes
    # red before Xcode does.
    init = re.search(r"extension BackendReportListItem: Decodable \{(.*?)\n\}", src, re.S)
    assert init and "decodeIfPresent(String.self, forKey: .processingStartedAt)" in init.group(1)


def test_the_timeout_pass_uses_two_clocks():
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    body = re.search(r"private func applyClientSideTimeoutPass\(\) \{(.*?)\n    \}", src, re.S)
    assert body, "applyClientSideTimeoutPass not found"
    b = body.group(1)
    assert "report.processingStartedAt" in b and "startedTimeoutSeconds" in b
    assert "report.date" in b and "queuedTimeoutSeconds" in b
    assert "processingTimeoutSeconds" not in src, "the single 600 s created_at clock is back"
    started = re.search(r"startedTimeoutSeconds: TimeInterval = (\d+)", src)
    queued = re.search(r"queuedTimeoutSeconds: TimeInterval = (\d+)", src)
    assert started and queued
    from app.config import settings
    assert int(started.group(1)) > settings.RESEARCH_PIPELINE_TIMEOUT_SECONDS, (
        "the started clock must sit PAST the server's kill, or the client flips first")
    assert int(queued.group(1)) > int(started.group(1))
    # The queued clock must not RACE the server's own queue-abandon window: at 1,800 s the
    # client flipped a healthy queued report 2.7 h before the sweep would, the flip stopped
    # the poll, the report completed unseen, and Retry deleted it unrefunded and charged
    # again. Pinned against the SAME derivation the server uses, not a copied number.
    from app.services import research_reconciliation_service as recon
    assert int(queued.group(1)) >= recon.RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS, (
        f"queuedTimeoutSeconds {queued.group(1)} < the server's "
        f"RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS {recon.RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS}"
    )


def _brace_block(src: str, opener: str) -> str:
    """The body of the first `opener ... {` in `src`, brace-balanced (comments stripped)."""
    i = src.index(opener)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        c = src[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[j + 1:k]
        k += 1
    raise AssertionError(f"unbalanced braces after {opener!r}")


def test_a_locally_flipped_card_keeps_the_list_poll_alive():
    """A card this client flipped is not terminal on the server; it heals only by
    re-reading. The poll used to exit the moment nothing was `.processing` — exactly when
    the flipped card most needed it."""
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    body = _brace_block(src, "func startReportsPolling()")
    predicate = body[body.index("let hasInflight"):body.index("if !hasInflight")]
    assert "locallyTimedOutReportIds" in predicate, predicate


def test_retry_checks_the_server_before_it_deletes_and_deletes_with_retry_intent():
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    body = _brace_block(src, "func retryReport(_ report: AnalysisReport)")
    check = body.index("serverSaysCompleted(backendId)")
    delete = body.index(".deleteReport(reportId: backendId, forRetry: true)")
    assert check < delete, "the status pre-check must precede the DELETE"
    # The server's 409 for the race the pre-check can lose is handled, not surfaced.
    assert 'code == "REPORT_ALREADY_COMPLETED"' in body[delete:]
    assert "adoptCompletedInsteadOfRetrying(backendId)" in body
    # And the refund the DELETE just issued is adopted BEFORE the balance gate runs.
    credits = body.index("await self.loadCredits()", delete)
    assert credits < body.index("self.generateAnalysis()")
    assert "self.creditBalance = nil" in body[delete:credits]


def test_the_retry_delete_carries_the_intent_on_the_wire():
    src = _strip_swift_comments(_API_ENDPOINT.read_text(encoding="utf-8"))
    assert "case deleteReport(reportId: String, forRetry: Bool = false)" in src
    q = _brace_block(src, "nonisolated var queryParameters: [String: String]?")
    arm = q[q.index("case .deleteReport(_, let forRetry):"):]
    arm = arm[:arm.index("case .", 5)]
    assert '["intent": "retry"]' in arm and "nil" in arm


def test_the_backend_delete_reads_the_same_intent():
    from app.api.v1.endpoints import research as ep
    import inspect
    src = inspect.getsource(ep.delete_report)
    assert 'lower() == "retry"' in src and 'status_before == "completed"' in src
    assert "ErrorCode.REPORT_ALREADY_COMPLETED" in src


def test_deleting_your_own_generating_card_is_not_an_error():
    """The monitor's next poll reads `deleted` — the outcome the user asked for. It used
    to pop an "Error: This analysis is no longer available" alert over the cleaned list."""
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    body = _brace_block(src, "func generateAnalysis()")
    failed = body[body.index("case .failed(let appError):"):]
    guard = failed.index("self.dismissedReportIds.contains(id)")
    deleted_code = failed.index('code == "RESEARCH_DELETED"')
    surfaced = failed.index("self.error = appError.message")
    tracked = failed.index("Analytics.shared.track(.reportFailed")
    assert guard < tracked and deleted_code < tracked, "a deletion must not log reportFailed"
    assert guard < surfaced and deleted_code < surfaced, "a deletion must not raise the alert"
    assert "continue" in failed[guard:tracked]


def test_the_model_threads_the_stamp_through():
    src = _strip_swift_comments(_MODEL.read_text(encoding="utf-8"))
    assert "var processingStartedAt: Date? = nil" in src
    assert "parseISO(item.processingStartedAt)" in src
    # withClientTimeout must preserve it, or a flipped card loses its clock on the next pass.
    body = re.search(r"func withClientTimeout\(\) -> AnalysisReport \{(.*?)\n    \}", src, re.S)
    assert body and "processingStartedAt: processingStartedAt" in body.group(1)
