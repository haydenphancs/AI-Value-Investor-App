import SwiftUI

struct ResearchListView: View {
    @StateObject private var viewModel = ResearchListViewModel()

    var body: some View {
        NavigationView {
            ZStack {
                if viewModel.isLoading {
                    LoadingView(message: "Loading reports...")
                } else if viewModel.reports.isEmpty {
                    EmptyStateView(
                        icon: "doc.text.magnifyingglass",
                        title: "No Research Reports",
                        message: "Generate your first AI-powered research report to get started.",
                        actionTitle: "Search Stocks",
                        action: {
                            // Navigate to search
                        }
                    )
                } else {
                    List {
                        ForEach(viewModel.reports) { report in
                            NavigationLink(destination: ResearchDetailView(reportId: report.id)) {
                                ResearchReportRow(report: report)
                            }
                        }
                        .onDelete { indexSet in
                            for index in indexSet {
                                let report = viewModel.reports[index]
                                Task {
                                    await viewModel.deleteReport(report)
                                }
                            }
                        }
                    }
                    .refreshable {
                        await viewModel.loadReports()
                    }
                }
            }
            .navigationTitle("Research Reports")
            .task {
                await viewModel.loadReports()
            }
        }
    }
}

struct ResearchReportRow: View {
    let report: ResearchReport

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
            HStack {
                Text(report.personaEmoji)
                    .font(.title2)

                VStack(alignment: .leading, spacing: 4) {
                    if let title = report.title {
                        Text(title)
                            .font(.headline)
                            .lineLimit(2)
                    }

                    Text(report.personaDisplayName)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Spacer()

                StatusBadge(status: report.status)
            }

            if let summary = report.executiveSummary {
                Text(summary.truncated(to: 150))
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(3)
            }

            HStack {
                Text(report.createdAt.timeAgo())
                    .font(.caption)
                    .foregroundColor(.secondary)

                Spacer()

                if let rating = report.userRating {
                    HStack(spacing: 2) {
                        ForEach(0..<rating, id: \.self) { _ in
                            Image(systemName: "star.fill")
                                .font(.caption)
                                .foregroundColor(.yellow)
                        }
                    }
                }
            }
        }
        .padding(.vertical, 4)
    }
}

struct StatusBadge: View {
    let status: ReportStatus

    var body: some View {
        Text(status.displayName)
            .font(.caption)
            .fontWeight(.medium)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(backgroundColor.opacity(0.2))
            .foregroundColor(textColor)
            .cornerRadius(AppConstants.cornerRadiusSmall)
    }

    private var backgroundColor: Color {
        switch status {
        case .completed: return .green
        case .processing, .pending: return .blue
        case .failed: return .red
        }
    }

    private var textColor: Color {
        switch status {
        case .completed: return .green
        case .processing, .pending: return .blue
        case .failed: return .red
        }
    }
}

struct ResearchListView_Previews: PreviewProvider {
    static var previews: some View {
        ResearchListView()
    }
}
