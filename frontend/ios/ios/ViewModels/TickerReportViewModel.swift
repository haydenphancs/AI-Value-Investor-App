//
//  TickerReportViewModel.swift
//  ios
//
//  ViewModel for the Ticker Report screen - MVVM Architecture
//  Fetches real data from GET /stocks/{ticker}/report backend endpoint.
//

import Foundation
import Combine

@MainActor
class TickerReportViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var reportData: TickerReportData?
    @Published var isLoading: Bool = false
    @Published var error: String?

    /// What the error view's primary button should DO. Defaults to `.retry`, which is right
    /// for the transient majority (network, 5xx, generation timeout) and is what the screen
    /// offered unconditionally before. Set from `AppError.from(_:).suggestedAction` so an
    /// error that retrying cannot fix — chiefly a 402 out-of-credits — routes somewhere that
    /// can actually resolve it.
    @Published var errorAction: ErrorAction = .retry

    /// A failure that must NOT replace the report already on screen — chiefly a failed
    /// pull-to-refresh.
    ///
    /// `body` renders `reportData` before `error`, so setting `error` while a report is
    /// loaded produced NOTHING: the user pulled down, the request failed, the spinner
    /// retracted, and the app said nothing at all. A silent failure on a user-initiated
    /// action is exactly what the app-wide rule forbids. This drives a dismissible
    /// banner over the existing content instead.
    @Published var transientError: String?

    // AI chat input (bound directly to CaydexAIChatBar in the View). The bar's onSend now seeds
    // the unified full-screen chat (AIChatScreen) with report context — see TickerReportView.
    @Published var aiInputText: String = ""

    // MARK: - Private Properties
    private let ticker: String
    private let persona: String

    /// The backend persona key the report was generated with (e.g. "warren_buffett").
    /// Exposed so the report chat can build a `"TICKER|persona"` reference_id that
    /// hits the same `ticker_report_cache` row for backend context grounding.
    var personaKey: String { persona }
    /// Backend research_reports row ID. When present, the fetch path
    /// prefers the cached `ticker_report_data` JSONB (instant) over a
    /// fresh /stocks/{ticker}/report call (~30-60s + FMP cost).
    private let reportId: String?
    private var loadAttempts: Int = 0

    // Deep Dive Modules - stored once to avoid regenerating UUIDs on every access
    let deepDiveModules: [DeepDiveModule] = [
        DeepDiveModule(title: "Recent Price Movement", iconName: "chart.xyaxis.line", type: .recentPriceMovement),
        DeepDiveModule(title: "The Revenue Engine", iconName: "dollarsign.circle", type: .revenueEngine),
        DeepDiveModule(title: "Fundamentals & Growth", iconName: "chart.bar.fill", type: .fundamentalsGrowth),
        DeepDiveModule(title: "Future Forecast", iconName: "binoculars.fill", type: .futureForecast),
        DeepDiveModule(title: "Insider & Management", iconName: "person.2.fill", type: .insiderManagement),
        DeepDiveModule(title: "Hidden Market Signals", iconName: "eye.fill", type: .hiddenMarketSignals),
        DeepDiveModule(title: "Industry & Competitive Moat", iconName: "shield.fill", type: .moatCompetition),
        DeepDiveModule(title: "Macro-Economic & Geopolitical", iconName: "globe", type: .macroGeopolitical),
        DeepDiveModule(title: "Wall Street Consensus", iconName: "building.columns.fill", type: .wallStreetConsensus)
    ]

    // MARK: - Initialization
    init(ticker: String, persona: String = "warren_buffett") {
        self.ticker = ticker
        self.persona = persona
        self.reportId = nil
        loadReport()
    }

    /// Init from a Reports-tab `AnalysisReport`. Carries the backend
    /// row ID so we can hit the cached ticker_report_data JSONB and
    /// preserves the persona the report was generated with.
    init(report: AnalysisReport) {
        self.ticker = report.ticker
        self.persona = report.persona.backendKey
        self.reportId = report.backendId
        loadReport()
    }

    /// Preview-only initializer: sets data synchronously, no async Task.
    init(ticker: String, preloadedReport: TickerReportData) {
        self.ticker = ticker
        self.persona = "warren_buffett"
        self.reportId = nil
        self.reportData = preloadedReport
        self.isLoading = false
    }

    // MARK: - Data Loading

    func loadReport() {
        // The direct report path runs the SAME pipeline for the same cost as
        // /research/generate on a cache miss (~17 Gemini + ~20 FMP calls), so it carries the
        // same account requirement. Gating only /research/generate would have made that gate
        // cosmetic — this is the other door into the identical spend.
        guard AppActions.shared.isSignedIn else {
            AppActions.shared.requestSignIn(for: "view AI analysis")
            isLoading = false
            return
        }

        isLoading = true
        error = nil
        transientError = nil
        // Reset the ACTION too. It is sticky state — a previous 402 leaves `.upgrade`
        // behind, so the next unrelated failure (a timeout, say) offered "Get Credits"
        // to a user with a full balance and no way to retry.
        errorAction = .retry
        loadAttempts += 1

        Task { [weak self] in
            guard let self = self else { return }
            await self._fetchReport()
        }
    }

    /// True when a refresh found no cached report and the only way forward COSTS 20 CREDITS.
    /// The view renders an explicit "Regenerate" affordance; nothing spends until it is tapped.
    @Published var needsPaidRegeneration: Bool = false

    /// Pull-to-refresh. **Never bills.**
    ///
    /// This used to call `_fetchReport()` directly, which skips `loadReport`'s sign-in gate and
    /// falls through to the BILLABLE Path B on a cache miss. That is not a rare state: the
    /// backend cache is close-ALIGNED, not a rolling TTL — `is_cache_fresh` compares against
    /// the most recent weekday 6:00pm ET — so a report generated at 5:55pm ET is stale at
    /// 6:00pm. Open the screen, wait ten minutes, pull down, and 20 credits are gone with no
    /// dialog and no cost disclosure. Same session, no backgrounding needed.
    ///
    /// A pull gesture is a reflex. It must not be able to spend money, so a miss surfaces the
    /// cost and waits for a deliberate tap instead. That removes the whole class rather than
    /// putting a modal in front of a gesture people repeat.
    func refresh() async {
        guard AppActions.shared.isSignedIn else {
            AppActions.shared.requestSignIn(for: "view AI analysis")
            return
        }
        transientError = nil
        await _fetchReport(allowPaidGeneration: false)
    }

    /// The deliberate, disclosed spend. Only reachable from the "Regenerate · N credits"
    /// button the view shows when `needsPaidRegeneration` is true.
    func regenerateForCredits() async {
        guard AppActions.shared.isSignedIn else {
            AppActions.shared.requestSignIn(for: "generate AI analysis")
            return
        }
        needsPaidRegeneration = false
        isLoading = true
        await _fetchReport(allowPaidGeneration: true)
    }

    /// The backend `ErrorCode`s that mean "this stored report isn't available", as opposed to
    /// "we couldn't reach it". Only these justify falling through to the BILLABLE live fetch.
    ///
    /// Sourced from `research.py::get_research_ticker_report`, which documents its own
    /// contract: REPORT_NOT_FOUND (no row), REPORT_NOT_READY (still generating),
    /// DATA_INCOMPLETE (completed but the JSONB column was empty). Keep in step with it —
    /// `/list-error-codes` verifies the enum on both sides.
    private static let fallthroughErrorCodes: Set<String> = [
        "REPORT_NOT_FOUND",
        "REPORT_NOT_READY",
        "DATA_INCOMPLETE",
    ]

    /// True when the stored report is genuinely absent, so regenerating is the right move.
    ///
    /// Everything else — network, timeout, 5xx, rate-limit, decode, auth — is a TRANSPORT or
    /// CONTRACT failure. Those must surface, because falling through spends the user's money
    /// on a report they already paid for.
    static func reportIsGenuinelyUnavailable(_ error: Error) -> Bool {
        guard let apiError = error as? APIError else { return false }
        switch apiError {
        case .notFound:
            return true
        case .businessError(let code, _):
            return fallthroughErrorCodes.contains(code)
        default:
            // .networkError, .decodingError, .serverError, .rateLimited, .unauthorized,
            // .forbidden, .unknown — all mean "we could not read it", not "it is not there".
            return false
        }
    }

    /// Core fetch logic — shared by loadReport() and refresh()
    private func _fetchReport(allowPaidGeneration: Bool = true) async {
        let attempt = self.loadAttempts
        print("📊 [TickerReport] Loading report for \(self.ticker) with persona \(self.persona) (attempt \(attempt))...")

        // Path A — cached JSONB on a known research_reports row.
        // This is the fast path when navigating from the Reports tab:
        // the report was already generated, the full TickerReportResponse
        // is stored in ticker_report_data, and this returns instantly
        // with zero new FMP/Gemini calls.
        if let reportId = self.reportId {
            do {
                let response: TickerReportAPIResponse = try await APIClient.shared.request(
                    endpoint: .getResearchTickerReport(reportId: reportId),
                    responseType: TickerReportAPIResponse.self
                )
                print("✅ [TickerReport] Cache hit via report \(reportId) for \(response.symbol) (persona=\(self.persona))")
                self.reportData = response.toTickerReportData()
                self.error = nil
                self.isLoading = false
                return
            } catch {
                // ⚠️ FALL THROUGH ONLY WHEN THE REPORT GENUINELY ISN'T THERE.
                //
                // Path B below is BILLABLE: on a cache miss it runs the full ~17 Gemini +
                // ~20 FMP pipeline and charges 20 credits. This `catch` used to be
                // unqualified, so ANY failure reaching it — a network blip, a timeout, a
                // transient 500, a rate-limit, or a Codable `decodingError` — silently
                // re-bought a report the user already owns.
                //
                // The realistic case: you open last week's report from the Reports tab, the
                // connection hiccups, that (ticker, persona) cache row has since rotated out
                // of the close cycle → 20 credits, no prompt, no way to tell it happened.
                // A DTO drift is worse still: the decode fails every time, so that report
                // charges on EVERY open, forever.
                //
                // The backend documents exactly three "not there" outcomes for this route
                // (research.py `get_research_ticker_report`), and only those may fall through.
                // A refresh may confirm the report is gone, but must never BUY a new one.
                // Surface the cost instead and let the user decide.
                if !allowPaidGeneration {
                    if Self.reportIsGenuinelyUnavailable(error) {
                        self.needsPaidRegeneration = true
                        self.error = nil
                        self.transientError = nil
                    } else {
                        // A refresh that failed for TRANSPORT reasons. The report the
                        // user is reading is still valid and still on screen, so this
                        // goes to the banner — writing `error` here set a field the
                        // view can never reach while `reportData` is non-nil.
                        let appError = AppError.from(error)
                        if self.reportData == nil {
                            self.error = appError.message
                            self.errorAction = appError.suggestedAction
                        } else {
                            self.transientError = appError.message
                        }
                    }
                    self.isLoading = false
                    return
                }
                if Self.reportIsGenuinelyUnavailable(error) {
                    print("⚠️ [TickerReport] Cached ticker_report_data unavailable for \(reportId): \(type(of: error)): \(error.localizedDescription). Falling back to live fetch.")
                } else {
                    print("❌ [TickerReport] Cached report \(reportId) failed to load (\(type(of: error)): \(error)) — NOT falling through to a billable regeneration.")
                    self.isLoading = false
                    if self.reportData == nil {
                        self.error = self.userFriendlyError(error)
                        // Same routing as Path B below — this branch used to leave
                        // `errorAction` at whatever a previous failure had set.
                        self.errorAction = AppError.from(error).suggestedAction
                    } else {
                        self.transientError = AppError.from(error).message
                    }
                    return
                }
            }
        }

        // Path B — generate (or hit the 24h ticker_report_data cache
        // by ticker+persona) via the public ticker-report endpoint.
        //
        // ⚠️ Guarded here too, not only in Path A's catch. When `reportId` is nil — the direct
        // navigation from search or the watchlist — Path A is skipped ENTIRELY, so a refresh
        // arrives straight here. Without this a pull-to-refresh on any ticker opened that way
        // bills on the first stale close-cycle boundary.
        if !allowPaidGeneration {
            self.needsPaidRegeneration = true
            self.isLoading = false
            return
        }

        do {
            let response: TickerReportAPIResponse = try await APIClient.shared.request(
                endpoint: .getTickerReport(ticker: self.ticker, persona: self.persona),
                responseType: TickerReportAPIResponse.self
            )

            print("✅ [TickerReport] Report loaded successfully for \(response.symbol)")
            print("   Quality Score: \(response.qualityScore)")
            print("   Agent: \(response.agent)")
            print("   Sections: fundamentals=\(response.fundamentalMetrics.count), criticalFactors=\(response.criticalFactors.count)")

            let reportData = response.toTickerReportData()
            self.reportData = reportData
            self.error = nil
            self.isLoading = false

        } catch {
            // Surface the underlying error type so future debugging
            // can distinguish APIError.notFound vs decoding vs network.
            print("❌ [TickerReport] Failed to load report: \(type(of: error)): \(error)")
            if let apiError = error as? APIError {
                print("   API Error: \(apiError)")
            }
            print("   Error details: \(error.localizedDescription)")

            self.isLoading = false
            self.error = self.userFriendlyError(error)
            // What the user can actually DO about it.
            //
            // The message copy below stays bespoke (it names the ticker, which
            // `AppError` cannot), but the ACTION is taken from `AppError.from(_:)` so this
            // screen agrees with every other surface about where an error sends you. It
            // used to be dropped entirely: a 402 INSUFFICIENT_CREDITS carries
            // `action: "upgrade"`, but the error view offered only Retry — which re-issues
            // the identical request and 402s again — with no route to Buy Credits. A user
            // out of credits was stuck in a loop on the one screen that could sell them the
            // fix (SYSTEM_DESIGN_GUIDELINES §9b.7).
            self.errorAction = AppError.from(error).suggestedAction
            // Don't set reportData — let the error view show with retry button
        }
    }

    // MARK: - Detailed-Analysis PDF

    /// Which PDF sheet to present (nil = none). `.view` opens the in-app viewer;
    /// `.share` opens the viewer and immediately offers the iOS share sheet.
    @Published var pdfSheet: PDFSheet?

    enum PDFSheet: Identifiable {
        case view, share
        var id: String { self == .view ? "view" : "share" }
    }

    /// The detailed-analysis PDF exists only for saved research reports (rows
    /// with a backend id). Direct/ad-hoc fetches (reportId == nil) can't export.
    var canExportPDF: Bool { reportId != nil }
    var pdfReportId: String? { reportId }

    /// The `research_reports` row this screen is rendering, when it has one.
    ///
    /// Sent to the chat so it can ground on the FROZEN report on screen rather than
    /// on `ticker_report_cache`, which is close-aligned and goes stale at the next
    /// weekday 18:00 ET — after which a saved report resolved to no context at all
    /// and the assistant silently answered from live market data instead.
    var backendReportId: String? { reportId }

    func shareTapped() {
        guard reportId != nil else { return }
        pdfSheet = .share
    }

    func viewDetailedAnalysis() {
        guard reportId != nil else { return }
        pdfSheet = .view
    }

    /// Soft-delete this report via DELETE /research/reports/{id}. Returns
    /// true on success so the caller can dismiss the screen. No-op when
    /// the report isn't backed by a research_reports row (reportId is nil
    /// for ad-hoc fetches from Trending Analyses, where there's nothing
    /// to delete on the server).
    func deleteReport() async -> Bool {
        guard let reportId = self.reportId else {
            print("🗑️ [TickerReport] Delete tapped but no reportId — skipping")
            return false
        }
        do {
            try await APIClient.shared.request(endpoint: .deleteReport(reportId: reportId))
            print("🗑️ [TickerReport] Report \(reportId) deleted")
            return true
        } catch {
            print("❌ [TickerReport] Delete failed: \(error)")
            self.error = self.userFriendlyError(error)
            return false
        }
    }


    // MARK: - Error Helpers

    private func userFriendlyError(_ error: Error) -> String {
        if let apiError = error as? APIError {
            switch apiError {
            case .networkError:
                return "Network error. Check your connection and make sure the backend is running."

            // Phase 3: backend now emits {error_code, user_message, …}
            // on the report-pipeline endpoints, surfaced as
            // .businessError. Route by code so users see actionable
            // copy (retry vs. wait vs. check symbol) and our logs
            // carry the underlying cause.
            case .businessError(let code, let message):
                switch code {
                case "TICKER_NOT_FOUND":
                    return "Ticker '\(ticker)' wasn't found. Check the symbol and try again."
                case "INVALID_PERSONA":
                    return "That investor persona isn't supported."
                case "INVALID_INPUT":
                    return message.isEmpty
                        ? "The request was invalid. Please try again."
                        : message
                case "FMP_RATE_LIMITED":
                    return "Market data is rate-limited right now. Please try again in a minute."
                case "FMP_UNAVAILABLE":
                    return "Our market data provider is temporarily unavailable. Try again shortly."
                case "GEMINI_QUOTA_EXCEEDED":
                    return "AI analysis quota exceeded. Please try again in a few minutes."
                case "GEMINI_UNAVAILABLE":
                    return "The AI analysis engine is temporarily unavailable. Try again shortly."
                case "DATA_INCOMPLETE":
                    return "We couldn't gather enough data for \(ticker) to produce a full report."
                case "REPORT_GENERATION_FAILED":
                    return "Report generation failed. Please try again."
                case "REPORT_NOT_FOUND":
                    return "That report no longer exists."
                case "REPORT_NOT_READY":
                    return "The report is still generating. Try again in a few seconds."
                case "INSUFFICIENT_CREDITS":
                    return "You're out of credits. Upgrade your tier or wait for the monthly reset."
                default:
                    // Unknown code — show backend's user_message
                    // verbatim so we still surface the cause without
                    // shipping an iOS update.
                    return message.isEmpty
                        ? "Something went wrong. Please try again."
                        : message
                }

            case .serverError(let code):
                return "Server error (\(code)). The AI report generation may have timed out. Try again."
            case .notFound:
                return "Ticker '\(ticker)' was not found. Check the symbol and try again."
            case .decodingError:
                return "Received unexpected data from the server. This is a bug — please report it."
            case .rateLimited(let retryAfter):
                return "You've hit a request limit. Try again in \(retryAfter)s."
            case .unauthorized:
                return "Your session expired. Please sign in again."
            case .forbidden:
                return "You don't have access to this. If this seems wrong, contact support."
            // The report endpoints are guest-capable, so neither of these should be reachable
            // here — but routing them through `AppError` rather than inventing local copy keeps
            // the wording identical to every other auth surface in the app.
            case .authRequired, .authError:
                return AppError.from(apiError).message
            case .unknown(let message):
                return message.isEmpty
                    ? "Something went wrong. Please try again."
                    : message
            }
        }
        return "Could not load report. Please check your connection and try again."
    }
}

// MARK: - Chat Response DTO

struct TickerReportChatResponse: Codable {
    let reply: String
    let ticker: String

    enum CodingKeys: String, CodingKey {
        case reply, ticker
    }
}
