//
//  AssetsPlaceholderCard.swift
//  ios
//
//  Molecule: what the Holdings list shows when it has no rows to show.
//
//  Distinguishes the two states an empty list used to conflate. Before this, a
//  failed feed and an empty portfolio rendered identically — nothing at all,
//  because AssetsListSection sizes itself to `count * rowHeight` and collapses to
//  a few points. "We couldn't load your holdings" and "you don't hold anything"
//  are different statements about the user's own money; showing the second when
//  the first is true is misinformation, not just a missing affordance.
//
//  Domain-free (strings + closures in), so it lives at Molecule level and can be
//  reused by any list that needs the same empty-vs-failed split.
//

import SwiftUI

struct AssetsPlaceholderCard: View {
    /// Non-nil ⇒ the load FAILED. Already mapped through `AppError` by the
    /// ViewModel — never a raw backend string.
    let errorMessage: String?
    var isLoading: Bool = false
    var onRetry: (() -> Void)?
    var onAdd: (() -> Void)?

    private var isError: Bool { errorMessage != nil }

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: isError ? "exclamationmark.triangle" : "chart.line.uptrend.xyaxis")
                .font(.system(size: 28))
                .foregroundColor(isError ? AppColors.neutral : AppColors.textMuted)

            Text(isError ? "Couldn't load your holdings" : "No tickers yet")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Text(errorMessage ?? "Add a ticker to start tracking prices, alerts and portfolio insights.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            // Only ONE affordance, matched to the state: retrying an empty
            // portfolio does nothing, and offering "add a ticker" during an
            // outage sends the user into a flow that will also fail.
            if isError {
                Button {
                    onRetry?()
                } label: {
                    Text(isLoading ? "Retrying…" : "Retry")
                        .font(AppTypography.labelEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        .background(AppColors.cardBackgroundLight)
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
                .disabled(isLoading)
            } else if onAdd != nil {
                Button {
                    onAdd?()
                } label: {
                    Text("Add a ticker")
                        .font(AppTypography.labelEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        .background(AppColors.cardBackgroundLight)
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xl)
        .padding(.horizontal, AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    VStack(spacing: 16) {
        AssetsPlaceholderCard(errorMessage: nil, onAdd: {})
        AssetsPlaceholderCard(
            errorMessage: "We couldn't load your holdings right now. Pull to refresh in a moment.",
            onRetry: {}
        )
    }
    .background(AppColors.background)
}
