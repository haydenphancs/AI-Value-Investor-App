//
//  ETFDividendHistoryView.swift
//  ios
//
//  Screen showing full dividend history for an ETF.
//  Fetches from GET /api/v1/etfs/{symbol}/dividends.
//

import SwiftUI

struct ETFDividendHistoryView: View {
    @Environment(\.dismiss) private var dismiss
    let symbol: String

    /// Preloaded dividends from the parent (shown immediately while API loads)
    let preloadedDividends: [ETFDividendPayment]

    @State private var dividends: [ETFDividendPayment] = []
    @State private var payFrequency: String = ""
    @State private var isLoading: Bool = true
    @State private var errorMessage: String?

    private let repository: StockRepository = .shared

    var body: some View {
        VStack(spacing: 0) {
            // Navigation header
            HStack {
                Button(action: { dismiss() }) {
                    Image(systemName: "chevron.down")
                        .font(AppTypography.iconDefault).fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()

                VStack(spacing: AppSpacing.xxs) {
                    Text("\(symbol) Dividend History")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    if !payFrequency.isEmpty && payFrequency != "—" {
                        Text("Pays \(payFrequency)")
                            .font(AppTypography.captionSmall)
                            .foregroundColor(AppColors.textMuted)
                    }
                }

                Spacer()

                // Invisible spacer to center title
                Image(systemName: "chevron.down")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(.clear)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)

            // Column headers
            HStack {
                Text("Ex-Div Date")
                    .font(AppTypography.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity, alignment: .leading)

                Text("Dividend Per Share")
                    .font(AppTypography.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .background(AppColors.cardBackground)

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)

            // Content
            if let error = errorMessage {
                VStack(spacing: AppSpacing.md) {
                    Image(systemName: "exclamationmark.triangle")
                        .font(AppTypography.iconLarge)
                        .foregroundColor(AppColors.textMuted)
                    Text(error)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(AppSpacing.xl)
            } else {
                // Dividend list
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(dividends) { payment in
                            HStack {
                                Text(payment.exDividendDate)
                                    .font(AppTypography.labelSmall)
                                    .foregroundColor(AppColors.textSecondary)
                                    .frame(maxWidth: .infinity, alignment: .leading)

                                Text(payment.dividendPerShare)
                                    .font(AppTypography.labelSmall)
                                    .fontWeight(.semibold)
                                    .foregroundColor(AppColors.textSecondary)
                                    .frame(maxWidth: .infinity, alignment: .trailing)
                            }
                            .padding(.horizontal, AppSpacing.lg)
                            .padding(.vertical, AppSpacing.md)

                            Rectangle()
                                .fill(AppColors.cardBackgroundLight.opacity(0.5))
                                .frame(height: 1)
                        }

                        // Total count footer
                        if !dividends.isEmpty {
                            Text("\(dividends.count) dividend payments")
                                .font(AppTypography.captionSmall)
                                .foregroundColor(AppColors.textMuted)
                                .padding(.top, AppSpacing.lg)
                                .padding(.bottom, AppSpacing.xxxl)
                        }
                    }
                }

                if isLoading {
                    ProgressView()
                        .tint(AppColors.textMuted)
                        .padding(.bottom, AppSpacing.md)
                }
            }
        }
        .background(AppColors.background)
        .preferredColorScheme(.dark)
        .onAppear {
            // Show preloaded data immediately
            dividends = preloadedDividends
        }
        .task {
            await fetchFullHistory()
        }
    }

    private func fetchFullHistory() async {
        do {
            let response = try await repository.getETFDividends(symbol: symbol)
            let fullDividends = response.toDisplayModels()

            await MainActor.run {
                self.dividends = fullDividends
                self.payFrequency = response.payFrequency
                self.isLoading = false
            }
            print("[DividendHistory] Loaded \(fullDividends.count) dividends for \(symbol)")
        } catch {
            print("[DividendHistory] Failed for \(symbol): \(error)")
            await MainActor.run {
                // Keep preloaded data if API fails
                if self.dividends.isEmpty {
                    self.errorMessage = "Unable to load dividend history."
                }
                self.isLoading = false
            }
        }
    }
}

#Preview {
    ETFDividendHistoryView(
        symbol: "SPY",
        preloadedDividends: ETFDetailData.sampleSPY.netYield.dividendHistory
    )
}
