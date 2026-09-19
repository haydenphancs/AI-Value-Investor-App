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
    assert "queuedTimeoutSeconds" in b
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


# ── F21-6: neither clock may subtract a SERVER stamp from the DEVICE's Date() ─────────
#
# `processing_started_at` and `created_at` are the server's `now()`; nothing in the client
# anchors to server time. A phone with "Set Automatically" off and its clock 15 min fast
# read `now - started` as 900 s on the first stamped list load — past the 660 s clock — and
# flipped every STARTED report to "failed" the moment it began; Retry then DELETED
# (refunded) the live run and started another that flipped the same way. Both clocks are
# now aged on the device clock from the instant this device FIRST SAW the row on that clock,
# so skew cancels; the undercount (≤ one poll interval) only delays the flip.


def _timeout_pass_body(src: str) -> str:
    return _brace_block(src, "private func applyClientSideTimeoutPass(serverTruth: Bool)")


def test_the_timeout_pass_never_ages_a_server_stamp_against_the_device_clock():
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    b = _timeout_pass_body(src)
    # The two subtractions that made a fast device clock flip a live report.
    assert not re.search(r"timeIntervalSince\(\s*started\s*\)", b), (
        "the started clock subtracts processingStartedAt from Date() again — a device clock "
        "15 min fast flips every started report on its first stamped load"
    )
    assert not re.search(r"timeIntervalSince\(\s*report\.date\s*\)", b), (
        "the queued clock subtracts created_at from Date() again"
    )
    assert "timeIntervalSinceNow" not in b and "Date().timeIntervalSince(report" not in b
    # No `Date(...)` built from a server field is subtracted anywhere in the pass: the ONLY
    # `timeIntervalSince` is against the device-time anchor.
    subtractions = re.findall(r"timeIntervalSince\(([^)]*)\)", b)
    assert subtractions == ["anchor"], subtractions
    # The stamp is read only to CHOOSE the clock.
    assert "let stamped = report.processingStartedAt != nil" in b
    assert "stamped ? startedTimeoutSeconds : queuedTimeoutSeconds" in b


def test_the_anchor_is_first_observation_on_the_device_clock_and_is_keyed_per_clock():
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    assert re.search(
        r"private var timeoutClockAnchors: \[String: \(stamped: Bool, firstSeen: Date\)\] = \[:\]",
        src), "timeoutClockAnchors must be view-model state keyed by backend id"
    b = _timeout_pass_body(src)
    assert "let now = Date()" in b
    # An existing anchor is reused ONLY on the same clock; a row that gains its stamp moves
    # from the queued clock to the started clock and must re-anchor, or it inherits up to
    # 12,000 s of queued age and flips the instant it starts.
    reuse = _brace_block(b, "if let existing = timeoutClockAnchors[backendId], existing.stamped == stamped")
    assert "anchor = existing.firstSeen" in reuse
    fresh = b[b.index(reuse) + len(reuse):]
    fresh = _brace_block(fresh, "else")
    assert "anchor = now" in fresh
    assert "timeoutClockAnchors[backendId] = (stamped: stamped, firstSeen: now)" in fresh
    assert "now.timeIntervalSince(anchor) > limit" in b


def test_the_anchors_are_pruned_on_server_truth_and_dropped_with_a_terminal_row():
    """Same leak the flag set had: the list endpoint hides deleted rows, so an anchor for a
    retired id would otherwise live for the rest of the process."""
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    b = _timeout_pass_body(src)
    terminal = _brace_block(b, "if report.status == .ready || report.status == .failed")
    guarded = _brace_block(terminal, "if serverTruth")
    assert "timeoutClockAnchors.removeValue(forKey: backendId)" in guarded
    assert "inFlightIds.insert(backendId)" in b
    tail = b[b.rindex("if serverTruth"):]
    prune = _brace_block(tail, "if serverTruth")
    assert "timeoutClockAnchors = timeoutClockAnchors.filter { inFlightIds.contains($0.key) }" in prune
    # …and NOT on a failed read, which would re-anchor every row and stall the flip.
    assert b.count("timeoutClockAnchors.filter") == 1


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


def test_retry_runs_the_refund_independent_guards_before_it_deletes_the_card():
    """F09-9: `generateAnalysis()`'s sign-in gate ran only AFTER the failed card had been
    dismissed and the row soft-deleted — a signed-out tap left neither report. The balance
    gate is moved up ONLY for an already-refunded row (its DELETE refunds nothing); for an
    unrefunded row the DELETE is what funds the retry, so the post-DELETE reload stays."""
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    body = _brace_block(src, "func retryReport(_ report: AnalysisReport)")
    dismiss = body.index("self.dismissedReportIds.insert(backendId)")
    delete = body.index(".deleteReport(reportId: backendId, forRetry: true)")
    sign_in = body.index("guard AppActions.shared.isSignedIn else")
    assert sign_in < dismiss < delete, "sign-in must be checked before the card is dismissed"
    refunded_gate = body.index("report.isRefunded")
    assert refunded_gate < dismiss, "the refunded-row balance gate runs before the dismissal"
    gate = body[refunded_gate:dismiss]
    assert "await self.loadCredits()" in gate and "balance.credits < self.analysisCost.credits" in gate
    assert "return" in gate
    # An unrefunded row keeps the post-DELETE reload (the refund it just received).
    assert "self.creditBalance = nil" in body[delete:]


def test_retry_cannot_be_double_tapped_during_its_pre_check():
    """W2 E-1: the F09-9 reorder put the card's dismissal behind an await, so the enabled
    Retry button stayed on screen during the credits read and a second tap ran the whole
    path again — two DELETEs, two 20-credit generations. The guard is taken SYNCHRONOUSLY
    at the tap, before the Task, and released on every exit of the Task."""
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    body = _brace_block(src, "func retryReport(_ report: AnalysisReport)")
    guard_idx = body.index("guard !retryInFlightIds.contains(id) else")
    insert_idx = body.index("retryInFlightIds.insert(id)")
    task_idx = body.index("Task { [weak self] in")
    assert guard_idx < insert_idx < task_idx, "the re-entry guard must be taken before the Task starts"
    task_body = body[task_idx:]
    assert "defer { if let backendId { self.retryInFlightIds.remove(backendId) } }" in task_body, \
        "every exit of the Task (return, error, success) must release the guard"
    assert task_body.index("defer {") < task_body.index("await ")
    assert "private var retryInFlightIds: Set<String> = []" in src


def test_a_post_timeout_is_not_the_benign_poll_timeout():
    """F09-10: `.timeout` with no `.started` yet can only be the POST to /research/generate
    — the row may have committed and charged 20 credits, or never arrived. It used to be
    classified as the poll's client-side timeout: no error, no card, and the next tap could
    charge again."""
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    body = _brace_block(src, "func generateAnalysis()")
    assert "if case .timeout = appError, startedId != nil {" in body, \
        "the benign arm must be qualified by startedId != nil"
    post = body[body.index("} else if case .timeout = appError {"):]
    post = post[:post.index("self.error = appError.message")]   # up to the generic arm
    assert "await self.loadReports()" in post and "await self.loadCredits()" in post
    assert '"post_timeout"' in post
    assert "$0.status == .processing" in post
    # W2 E-2: adoption is BY LIST. Inserting the id into `inFlightReportIds` burned one of
    # the four concurrency slots for the session — no monitor owned it, so nothing removed it.
    assert "inFlightReportIds.insert" not in post, post
    assert "liveProgress[" not in post
    assert "check your Reports before retrying" in post
    assert post.count("self.startReportsPolling()") >= 2, "both the adopt and the unconfirmed arm keep polling"


def test_the_deleted_code_has_one_definition_on_both_sides():
    """F21-8: TPM assembled `"RESEARCH_" + status.uppercased()` and the VM literal-matched
    `"RESEARCH_DELETED"`; only the VM side was pinned, so a rename of the prefix would have
    silently turned every deletion into a red "failed" alert."""
    tpm = _strip_swift_comments(_DTO.read_text(encoding="utf-8"))
    assert tpm.count('deletedCode = "RESEARCH_DELETED"') == 1
    fn = _brace_block(tpm, "nonisolated private static func terminalUnknown(")
    assert 'status.status == "deleted"' in fn and "Self.deletedCode" in fn
    vm = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    assert '"RESEARCH_DELETED"' not in vm, "the VM must reference TaskPollingManager.deletedCode, not the literal"
    assert "TaskPollingManager.deletedCode" in vm
    # The backend really answers `deleted` for a soft-deleted row.
    import inspect
    from app.api.v1.endpoints import research as ep
    assert '"deleted"' in inspect.getsource(ep.delete_report)


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
    deleted_code = failed.index("code == TaskPollingManager.deletedCode")
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


def test_the_list_endpoint_applies_the_schema_on_the_wire():
    """The schema pins above guard nothing unless the handler APPLIES the model (F12-10):
    rows used to leave as raw dicts, so a renamed column reached iOS un-validated."""
    from typing import List, get_args, get_origin

    from app.api.v1.endpoints.research import router

    route = next(r for r in router.routes if getattr(r, "path", "") == "/reports"
                 and "GET" in getattr(r, "methods", set()))
    model = route.response_model
    assert get_origin(model) is list or model is List[ResearchReportListItem]
    assert get_args(model) == (ResearchReportListItem,)


def test_every_selected_column_is_declared_on_the_model_and_required_fields_are_not_null():
    """`response_model` must never 500 the list: every selected column exists on the model
    (nothing is stripped) and every non-Optional field has a DB NOT NULL guarantee."""
    src = _ENDPOINT.read_text(encoding="utf-8")
    m = re.search(r'select\(\s*((?:"[^"]*"\s*)+)\)\.eq\("user_id"', src)
    assert m, "list select not found"
    cols = {c.strip() for c in "".join(re.findall(r'"([^"]*)"', m.group(1))).split(",") if c.strip()}
    fields = ResearchReportListItem.model_fields
    assert cols <= set(fields), cols - set(fields)
    required = {n for n, f in fields.items() if f.is_required()}
    assert required <= {"id", "ticker", "investor_persona", "status", "created_at"}, required
