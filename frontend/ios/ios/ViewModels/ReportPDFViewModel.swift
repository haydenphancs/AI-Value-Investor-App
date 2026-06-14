//
//  ReportPDFViewModel.swift
//  ios
//
//  ViewModel for the detailed-analysis PDF viewer. Downloads the server-rendered
//  PDF for a completed research report, writes it to a temp file, and exposes a
//  local URL for PDFKit / the share sheet.
//

import Foundation
import Combine

@MainActor
final class ReportPDFViewModel: ObservableObject {

    enum State {
        case loading
        case ready(URL)
        case error(String)
    }

    @Published private(set) var state: State = .loading

    private let reportId: String
    private let apiClient: APIClient

    init(reportId: String, apiClient: APIClient = .shared) {
        self.reportId = reportId
        self.apiClient = apiClient
    }

    func load() async {
        state = .loading
        do {
            let url = try await fetchPDF()
            state = .ready(url)
        } catch {
            // PDF not generated yet, or a stale failure: try one inline
            // regeneration, then re-fetch. This backfills reports created
            // before the PDF feature and recovers pdf_status == 'failed'.
            if isRecoverable(error) {
                do {
                    try await apiClient.request(
                        endpoint: .regenerateResearchReportPDF(reportId: reportId)
                    )
                    let url = try await fetchPDF()
                    state = .ready(url)
                    return
                } catch {
                    state = .error(AppError.from(error).message)
                    return
                }
            }
            state = .error(AppError.from(error).message)
        }
    }

    // MARK: - Private

    private func fetchPDF() async throws -> URL {
        let data = try await apiClient.downloadData(
            endpoint: .getResearchReportPDF(reportId: reportId)
        )
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("caydex_report_\(reportId).pdf")
        try data.write(to: url, options: .atomic)
        return url
    }

    /// Whether the failure means "PDF not built yet" — worth a one-shot
    /// regenerate. A missing report (REPORT_NOT_FOUND) is not recoverable here.
    private func isRecoverable(_ error: Error) -> Bool {
        guard let api = error as? APIError,
              case .businessError(let code, _) = api else { return false }
        return code == "REPORT_NOT_READY" || code == "DATA_INCOMPLETE"
    }
}
