import SwiftUI

struct ResearchDetailView: View {
    @StateObject private var viewModel: ResearchDetailViewModel

    init(reportId: String) {
        _viewModel = StateObject(wrappedValue: ResearchDetailViewModel(reportId: reportId))
    }

    var body: some View {
        ZStack {
            if viewModel.isLoading {
                LoadingView(message: "Loading report...")
            } else if let report = viewModel.report {
                ScrollView {
                    VStack(alignment: .leading, spacing: AppConstants.paddingLarge) {
                        // Header
                        ReportHeaderView(report: report)

                        // Executive Summary
                        if let summary = report.executiveSummary {
                            ReportSection(title: "Executive Summary", content: summary)
                        }

                        // Investment Thesis
                        if let thesis = report.investmentThesis {
                            InvestmentThesisView(thesis: thesis)
                        }

                        // Pros & Cons
                        if let pros = report.pros, let cons = report.cons {
                            ProsConsView(pros: pros, cons: cons)
                        }

                        // Moat Analysis
                        if let moat = report.moatAnalysis {
                            MoatAnalysisView(moat: moat)
                        }

                        // Valuation
                        if let valuation = report.valuationAnalysis {
                            ValuationAnalysisView(valuation: valuation)
                        }

                        // Risk Assessment
                        if let risks = report.riskAssessment {
                            RiskAssessmentView(risks: risks)
                        }

                        // Recommendation
                        if let recommendation = report.actionRecommendation {
                            RecommendationView(recommendation: recommendation)
                        }

                        // Rating
                        if report.isCompleted {
                            RatingView(viewModel: viewModel)
                        }

                        // Disclaimer
                        Text("This is for educational purposes only. Not financial advice.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .italic()
                            .padding()
                            .background(Color.yellow.opacity(0.1))
                            .cornerRadius(AppConstants.cornerRadiusSmall)
                    }
                    .padding()
                }
            }
        }
        .navigationTitle("Research Report")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await viewModel.loadReport()
        }
    }
}

struct ReportHeaderView: View {
    let report: ResearchReport

    var body: some View {
        VStack(spacing: AppConstants.paddingMedium) {
            HStack {
                Text(report.personaEmoji)
                    .font(.largeTitle)

                VStack(alignment: .leading) {
                    if let title = report.title {
                        Text(title)
                            .font(.title3)
                            .fontWeight(.bold)
                    }

                    Text(report.personaDisplayName)
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }

                Spacer()

                StatusBadge(status: report.status)
            }

            if let stock = report.stock {
                HStack {
                    AsyncImage(url: URL(string: stock.logoUrl ?? "")) { image in
                        image.resizable().scaledToFit()
                    } placeholder: {
                        Image(systemName: AppImages.logoPlaceholder)
                    }
                    .frame(width: 30, height: 30)

                    Text("\(stock.ticker) - \(stock.companyName)")
                        .font(.subheadline)

                    Spacer()
                }
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct ReportSection: View {
    let title: String
    let content: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
            Text(title)
                .font(.headline)

            Text(content)
                .font(.body)
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct InvestmentThesisView: View {
    let thesis: InvestmentThesis

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            Text("Investment Thesis")
                .font(.headline)

            Text(thesis.summary)
                .font(.body)

            if !thesis.keyDrivers.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Key Drivers")
                        .font(.subheadline)
                        .fontWeight(.medium)

                    ForEach(thesis.keyDrivers, id: \.self) { driver in
                        HStack(alignment: .top) {
                            Text("•")
                            Text(driver)
                                .font(.subheadline)
                        }
                    }
                }
            }

            HStack {
                VStack(alignment: .leading) {
                    Text("Time Horizon")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text(thesis.timeHorizon)
                        .font(.subheadline)
                }

                Spacer()

                VStack(alignment: .trailing) {
                    Text("Conviction")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text(thesis.convictionLevel.capitalized)
                        .font(.subheadline)
                }
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct ProsConsView: View {
    let pros: [String]
    let cons: [String]

    var body: some View {
        HStack(alignment: .top, spacing: AppConstants.paddingMedium) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Pros")
                    .font(.headline)
                    .foregroundColor(.green)

                ForEach(pros, id: \.self) { pro in
                    HStack(alignment: .top) {
                        Text("✓")
                            .foregroundColor(.green)
                        Text(pro)
                            .font(.subheadline)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            VStack(alignment: .leading, spacing: 8) {
                Text("Cons")
                    .font(.headline)
                    .foregroundColor(.red)

                ForEach(cons, id: \.self) { con in
                    HStack(alignment: .top) {
                        Text("✗")
                            .foregroundColor(.red)
                        Text(con)
                            .font(.subheadline)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct MoatAnalysisView: View {
    let moat: MoatAnalysis

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            Text("Competitive Moat")
                .font(.headline)

            HStack {
                Text("Moat Rating:")
                    .foregroundColor(.secondary)
                Text(moat.moatRating.capitalized)
                    .fontWeight(.medium)
            }

            Text(moat.competitivePosition)
                .font(.body)
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct ValuationAnalysisView: View {
    let valuation: ValuationAnalysis

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            Text("Valuation")
                .font(.headline)

            HStack {
                Text("Rating:")
                    .foregroundColor(.secondary)
                Text(valuation.valuationRating.capitalized)
                    .fontWeight(.medium)
            }

            if let margin = valuation.marginOfSafety {
                Text("Margin of Safety: \(margin)")
                    .font(.subheadline)
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct RiskAssessmentView: View {
    let risks: RiskAssessment

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            Text("Risk Assessment")
                .font(.headline)

            HStack {
                Text("Overall Risk:")
                    .foregroundColor(.secondary)
                Text(risks.overallRisk.capitalized)
                    .fontWeight(.medium)
            }

            if !risks.businessRisks.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Business Risks")
                        .font(.subheadline)
                        .fontWeight(.medium)

                    ForEach(risks.businessRisks, id: \.self) { risk in
                        HStack(alignment: .top) {
                            Text("•")
                            Text(risk)
                                .font(.caption)
                        }
                    }
                }
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct RecommendationView: View {
    let recommendation: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
            Text("Recommendation")
                .font(.headline)

            Text(recommendation.uppercased())
                .font(.title2)
                .fontWeight(.bold)
                .foregroundColor(color)
        }
        .frame(maxWidth: .infinity)
        .padding()
        .background(color.opacity(0.1))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }

    private var color: Color {
        switch recommendation.lowercased() {
        case "buy": return .green
        case "sell": return .red
        case "hold": return .orange
        default: return .blue
        }
    }
}

struct RatingView: View {
    @ObservedObject var viewModel: ResearchDetailViewModel
    @State private var selectedRating = 0
    @State private var showingRating = false

    var body: some View {
        VStack(spacing: AppConstants.paddingMedium) {
            if let rating = viewModel.report?.userRating {
                HStack {
                    Text("Your Rating:")
                        .foregroundColor(.secondary)

                    HStack(spacing: 4) {
                        ForEach(0..<rating, id: \.self) { _ in
                            Image(systemName: "star.fill")
                                .foregroundColor(.yellow)
                        }
                    }
                }
            } else {
                Button(action: { showingRating.toggle() }) {
                    Text("Rate this Report")
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color.accentColor)
                        .foregroundColor(.white)
                        .cornerRadius(AppConstants.cornerRadiusMedium)
                }
                .sheet(isPresented: $showingRating) {
                    NavigationView {
                        VStack {
                            Text("Rate this report")
                                .font(.headline)

                            HStack {
                                ForEach(1...5, id: \.self) { rating in
                                    Button(action: {
                                        selectedRating = rating
                                    }) {
                                        Image(systemName: rating <= selectedRating ? "star.fill" : "star")
                                            .font(.largeTitle)
                                            .foregroundColor(.yellow)
                                    }
                                }
                            }
                            .padding()

                            Button(action: {
                                Task {
                                    await viewModel.rateReport(rating: selectedRating, feedback: nil)
                                    showingRating = false
                                }
                            }) {
                                Text("Submit Rating")
                                    .frame(maxWidth: .infinity)
                                    .padding()
                                    .background(selectedRating > 0 ? Color.accentColor : Color.gray)
                                    .foregroundColor(.white)
                                    .cornerRadius(AppConstants.cornerRadiusMedium)
                            }
                            .disabled(selectedRating == 0)
                            .padding()
                        }
                        .navigationBarTitleDisplayMode(.inline)
                    }
                }
            }
        }
    }
}

struct ResearchDetailView_Previews: PreviewProvider {
    static var previews: some View {
        ResearchDetailView(reportId: "sample-id")
    }
}
