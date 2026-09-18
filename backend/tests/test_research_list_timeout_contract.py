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
    body = re.search(r"private func applyClientSideTimeoutPass\(serverTruth: Bool\) \{(.*?)\n    \}",
                     src, re.S)
    assert body, "applyClientSideTimeoutPass(serverTruth:) not found"
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


# ── The flag set must DRAIN, and a failed GET must not drain it ─────────────────────
#
# `locallyTimedOutReportIds` is what keeps the 5 s list poll alive (test above). Two
# defects lived on that: (1) nothing ever REMOVED an id — the list endpoint hides deleted
# rows, so a retried or bulk-deleted card's flag outlived it and the poll ran for the rest
# of the process (~720 GETs/h, every row's UUID re-minted each tick); (2) the FAILED-GET
# branch ran the pass over in-memory rows that carried this client's own flip and read the
# rendered `.failed` as server truth, removed the id, and killed the poll the fix relies on.


def test_a_successful_list_read_drains_the_flag_set_against_the_raw_server_list():
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    body = _brace_block(src, "func loadReports() async")
    do_block = _brace_block(body, "do")
    drain = do_block.index("locallyTimedOutReportIds.formIntersection(Set(backendReports.map(\\.id)))")
    # Against the RAW list — before the dismissed filter builds `self.reports` — so a
    # retry's pre-check still sees a row it has dismissed but not yet deleted.
    assert drain < do_block.index("self.reports = backendReports")
    assert drain < do_block.index("applyClientSideTimeoutPass(serverTruth: true)")


def test_a_failed_list_read_keeps_this_clients_own_flips():
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    body = _brace_block(src, "func loadReports() async")
    catch = body[body.index("} catch {"):]
    assert "applyClientSideTimeoutPass(serverTruth: false)" in catch, (
        "the catch branch must run the pass WITHOUT server truth — in-memory rows carry the "
        "local flip, and reading it as terminal removes the id and kills the poll"
    )
    assert "formIntersection" not in catch, "never drain on a failed read"
    # And the pass itself only clears on server truth.
    pass_body = _brace_block(src, "private func applyClientSideTimeoutPass(serverTruth: Bool)")
    terminal = _brace_block(pass_body, "if report.status == .ready || report.status == .failed")
    assert "if serverTruth" in terminal and "locallyTimedOutReportIds.remove(backendId)" in terminal
    guarded = _brace_block(terminal, "if serverTruth")
    assert "locallyTimedOutReportIds.remove(backendId)" in guarded


def test_a_cancelled_list_tick_is_not_a_sync_failure():
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    body = _brace_block(src, "func loadReports() async")
    catch = body[body.index("} catch {"):]
    assert catch.index("guard !appError.isCancellation else { return }") < \
        catch.index("Analytics.shared.track(.backgroundSyncFailed")


def test_every_dismiss_site_drops_the_flag_and_an_identity_change_resets_it():
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    # Retry: AFTER the DELETE succeeded — the `contains` check before it gates the
    # completed pre-check, so removing earlier would silently skip that guard.
    retry = _brace_block(src, "func retryReport(_ report: AnalysisReport)")
    delete = retry.index(".deleteReport(reportId: backendId, forRetry: true)")
    check = retry.index("self.locallyTimedOutReportIds.contains(backendId)")
    remove = retry.index("self.locallyTimedOutReportIds.remove(backendId)")
    assert check < delete < remove
    # Bulk delete: on the `.deleted` arm of the fan-out.
    bulk = _brace_block(src, "func deleteSelectedReports() async")
    deleted_arm = bulk[bulk.index("case .deleted:"):bulk.index("case .failed:")]
    assert "locallyTimedOutReportIds.remove(rid)" in deleted_arm
    # Identity change: everything per-account goes, and the poll stops.
    ident = _brace_block(src, "func handleIdentityChange(isActiveTab: Bool) async")
    gate = ident.index("guard isActiveTab else { return }")
    for line in ("stopReportsPolling()", "locallyTimedOutReportIds = []",
                 "dismissedReportIds = []", "inFlightReportIds = []", "liveProgress = [:]"):
        assert line in ident[:gate], f"{line} must run BEFORE the active-tab gate"


# ── Bulk delete: refund adoption and the completed-refusal ───────────────────────────


def test_bulk_delete_reloads_credits_after_the_fan_out():
    """Every in-flight DELETE refunds server-side; the local balance was read before it, and
    `generateAnalysis` gates on the local number — so the Generate button refused work the
    server would accept until a pull-to-refresh."""
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    bulk = _brace_block(src, "func deleteSelectedReports() async")
    group_end = bulk.index("await withTaskGroup") + len(_brace_block(bulk, "await withTaskGroup"))
    tail = bulk[group_end:]
    assert tail.index("creditBalance = nil") < tail.index("await loadCredits()")


def test_bulk_delete_sends_the_retry_intent_only_for_client_flipped_cards_and_keeps_a_finished_one():
    """A card flipped on the CLIENT clock may have completed on the server; a plain DELETE
    soft-deletes that row unrefunded. The retry intent makes the server refuse with
    REPORT_ALREADY_COMPLETED, and that answer must be 'kept', not 'couldn't delete'."""
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    bulk = _brace_block(src, "func deleteSelectedReports() async")
    assert "let clientFlipped = locallyTimedOutReportIds.intersection(ids)" in bulk
    group = _brace_block(bulk, "await withTaskGroup")
    assert "let forRetry = clientFlipped.contains(rid)" in group
    assert ".deleteReport(reportId: rid, forRetry: forRetry)" in group
    assert 'code == "REPORT_ALREADY_COMPLETED"' in group
    assert "return (rid, .completedInstead)" in group
    kept = bulk[bulk.index("case .completedInstead:"):]
    kept = kept[:kept.index("}")]
    assert "dismissedReportIds.remove(rid)" in kept and "locallyTimedOutReportIds.remove(rid)" in kept
    assert "failedIds.append(rid)" not in kept, "a finished report is not a failed delete"


# ── The status monitor tolerates a transient miss ───────────────────────────────────


def test_one_transient_status_poll_miss_does_not_end_the_monitor():
    """A phone unlock (-1005), a deploy's 502s or a rate-limit used to surface "check your
    internet connection" as the ANALYSIS failing, log `reportFailed`, and free a slot the
    server still held — while the card kept advancing through the list poll."""
    src = _strip_swift_comments(_DTO.read_text(encoding="utf-8"))
    for fn in ("func generateAndMonitorResearch(", "func monitorResearch(reportId: String)"):
        body = _brace_block(src, fn)
        loop = _brace_block(body, "while true")
        # The status GET has its own catch that `continue`s on a transient failure.
        status = loop.index(".getResearchStatus(reportId: reportId)")
        arm = loop[status:loop.index("if status.isProcessing")]
        assert "Self.isTransientPollFailure(appError)" in arm and "continue" in arm, fn
        # And the detail GET after `completed` too.
        detail = loop.index(".getResearchReport(reportId: reportId)")
        arm = loop[detail:loop.index("continuation.yield(.completed(report))")]
        assert "Self.isTransientPollFailure(appError)" in arm and "continue" in arm, fn
    # Classifier: the transient set, and the decode drift excluded by the exact message
    # AppError gives it — pinned on both sides so they cannot drift apart.
    fn = _brace_block(src, "nonisolated static func isTransientPollFailure(_ error: AppError) -> Bool")
    for case in (".noConnection", ".timeout", ".serverError", ".rateLimited", ".authUnavailable",
                 ".signInRequired"):
        assert case in fn, case
    assert "message != Self.decodeFailureMessage" in fn
    assert 'static let decodeFailureMessage = "Failed to process server response"' in src
    app_error = _strip_swift_comments(
        (_REPO / "frontend/ios/ios/Core/Utilities/AppError.swift").read_text(encoding="utf-8"))
    decode_arm = app_error[app_error.index("case .decodingError:"):]
    decode_arm = decode_arm[:decode_arm.index("case .networkError")]
    assert '.unknown(message: "Failed to process server response")' in decode_arm
    # A cancelled monitor is not a failure the VM should alert on.
    assert "if error is CancellationError" in app_error
    vm = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    failed = _brace_block(vm, "func generateAnalysis()")
    failed = failed[failed.index("case .failed(let appError):"):]
    assert failed.index("if appError.isCancellation") < failed.index("Analytics.shared.track(.reportFailed")
