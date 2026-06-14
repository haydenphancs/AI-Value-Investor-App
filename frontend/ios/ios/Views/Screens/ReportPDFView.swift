//
//  ReportPDFView.swift
//  ios
//
//  Screen: in-app viewer for the detailed-analysis PDF. Downloads the
//  server-rendered report, shows it in PDFKit, and offers the iOS share sheet.
//  Presented from the TickerReport overflow menu (View Detailed Analysis / Share).
//

import SwiftUI

struct ReportPDFView: View {
    let reportId: String
    /// When true (the "Share" action), the share sheet is offered automatically
    /// once the PDF is ready, on top of the in-app viewer.
    let autoShare: Bool

    @StateObject private var viewModel: ReportPDFViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var sharePayload: SharePayload?

    init(reportId: String, autoShare: Bool = false) {
        self.reportId = reportId
        self.autoShare = autoShare
        _viewModel = StateObject(wrappedValue: ReportPDFViewModel(reportId: reportId))
    }

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("Detailed Analysis")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Done") { dismiss() }
                    }
                    ToolbarItem(placement: .primaryAction) {
                        if case .ready(let url) = viewModel.state {
                            Button {
                                sharePayload = SharePayload(url: url)
                            } label: {
                                Image(systemName: "square.and.arrow.up")
                            }
                        }
                    }
                }
                .sheet(item: $sharePayload) { payload in
                    ShareSheet(items: [payload.url])
                }
        }
        .task { await viewModel.load() }
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.state {
        case .loading:
            VStack(spacing: 14) {
                ProgressView()
                Text("Preparing your report…")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

        case .ready(let url):
            PDFKitView(url: url)
                .ignoresSafeArea(edges: .bottom)
                .onAppear {
                    if autoShare && sharePayload == nil {
                        sharePayload = SharePayload(url: url)
                    }
                }

        case .error(let message):
            VStack(spacing: 14) {
                Image(systemName: "doc.questionmark")
                    .font(.largeTitle)
                    .foregroundStyle(.secondary)
                Text(message)
                    .font(.subheadline)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
                Button("Retry") {
                    Task { await viewModel.load() }
                }
                .buttonStyle(.borderedProminent)
            }
            .padding(32)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}

/// Identifiable wrapper so the share sheet can be driven by `.sheet(item:)`
/// without making `URL` itself Identifiable app-wide.
private struct SharePayload: Identifiable {
    let id = UUID()
    let url: URL
}
