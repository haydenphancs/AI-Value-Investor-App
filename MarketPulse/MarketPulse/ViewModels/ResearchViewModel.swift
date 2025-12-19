import SwiftUI

@MainActor
class ResearchListViewModel: ObservableObject {
    @Published var reports: [ResearchReport] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiService = APIService()

    func loadReports() async {
        isLoading = true
        errorMessage = nil

        do {
            reports = try await apiService.getResearchReports()
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func deleteReport(_ report: ResearchReport) async {
        do {
            try await apiService.deleteReport(reportId: report.id)
            reports.removeAll { $0.id == report.id }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

@MainActor
class ResearchGenerationViewModel: ObservableObject {
    @Published var selectedPersona: InvestorPersona?
    @Published var isGenerating = false
    @Published var generatedReport: ResearchReport?
    @Published var errorMessage: String?

    private let apiService = APIService()
    let stockId: String

    init(stockId: String) {
        self.stockId = stockId
    }

    func generateReport() async {
        guard let persona = selectedPersona else { return }

        isGenerating = true
        errorMessage = nil

        do {
            let request = ResearchReportCreate(stockId: stockId, investorPersona: persona)
            generatedReport = try await apiService.generateReport(request)
        } catch {
            errorMessage = error.localizedDescription
        }

        isGenerating = false
    }
}

@MainActor
class ResearchDetailViewModel: ObservableObject {
    @Published var report: ResearchReport?
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiService = APIService()
    let reportId: String

    init(reportId: String) {
        self.reportId = reportId
    }

    func loadReport() async {
        isLoading = true
        errorMessage = nil

        do {
            report = try await apiService.getResearchReportDetail(reportId: reportId)
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func rateReport(rating: Int, feedback: String?) async {
        do {
            let ratingRequest = ResearchReportRate(userRating: rating, userFeedback: feedback)
            report = try await apiService.rateReport(reportId: reportId, rating: ratingRequest)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
