//
//  TrendingAnalysisDetailView.swift
//  ios
//
//  Screen: Detail view for a trending analysis topic
//  Shows sector overview, list of companies, and analysis stats
//

import SwiftUI

struct TrendingAnalysisDetailView: View {
    let analysis: TrendingAnalysis
    var onAnalyzeTicker: ((String) -> Void)?
    @Environment(\.dismiss) private var dismiss
    /// Stable token keying this screen's audio overlay host registration.
    @State private var compactToken = UUID().uuidString

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Header
                headerSection

                ScrollView(showsIndicators: false) {
                    // A plain VStack, NOT LazyVStack - see HomeDashboardView.content for the full write-up.
                    // The direct children here are a fixed, hand-written list, so laziness bought nothing,
                    // while a lazy stack whose child RESIZES IN PLACE re-walks its predecessor chain and can
                    // wedge the main thread at 100% inside LazySubviewPlacements -> _ViewList_Node.applyNodes.
                    //
                    // Nothing resizes today; kept eager so a future conditional cannot silently re-arm it.
                    VStack(spacing: AppSpacing.xxl) {
                        // Hero section
                        heroSection

                        // Stats row
                        statsSection

                        // Companies list
                        companiesSection

                        Spacer()
                            .frame(height: AppSpacing.xxxl)
                    }
                    .padding(.top, AppSpacing.lg)
                }
            }
        }
        .navigationBarHidden(true)
        // Keep the audio player visible above this fullScreenCover (bottom mini player).
        .globalAudioOverlay(token: compactToken, showBottomMiniPlayer: true)
    }

    // MARK: - Header

    private var headerSection: some View {
        HStack {
            NavBackButton(font: AppTypography.iconDefault, alignment: .leading) {
                dismiss()
            }

            Spacer()

            Text(analysis.title)
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)

            Spacer()

            // Invisible spacer to balance the back button. It has to match the
            // button's 44pt BOX, not the glyph inside it — a bare glyph here
            // would be ~17pt narrower and pull the centred title off-centre.
            Color.clear
                .frame(width: NavBackButton.hitTarget, height: NavBackButton.hitTarget)
                .accessibilityHidden(true)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
    }

    // MARK: - Hero

    private var heroSection: some View {
        VStack(spacing: AppSpacing.md) {
            ZStack {
                Circle()
                    .fill(analysis.iconBackgroundColor.opacity(0.2))
                    .frame(width: 72, height: 72)

                Image(systemName: analysis.systemIconName)
                    .font(.system(size: 32, weight: .semibold))
                    .foregroundColor(analysis.iconBackgroundColor)
            }

            Text(analysis.description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xxl)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Stats

    private var statsSection: some View {
        HStack(spacing: AppSpacing.md) {
            statCard(
                value: "\(analysis.companiesCount)",
                label: "Companies",
                iconName: "building.2.fill",
                color: analysis.iconBackgroundColor
            )

            statCard(
                value: "+\(analysis.interestPercent)%",
                label: "Interest",
                iconName: "arrow.up.right",
                color: AppColors.gain
            )
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    private func statCard(value: String, label: String, iconName: String, color: Color) -> some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: iconName)
                .font(AppTypography.iconDefault).fontWeight(.semibold)
                .foregroundColor(color)

            Text(value)
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
    }

    // MARK: - Companies

    private var companiesSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Key Players")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            LazyVStack(spacing: AppSpacing.sm) {
                ForEach(analysis.companies) { company in
                    companyRow(company)
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    private func companyRow(_ company: TrendingCompany) -> some View {
        HStack(spacing: 0) {
            // Left: Identity
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(company.ticker)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text(company.name)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
            }
            .frame(width: 150, alignment: .leading)

            // Middle: Market Pulse
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(company.price)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text(company.marketCap)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
            .frame(width: 90, alignment: .leading)

            Spacer()

            // Right: Action
            Button(action: {
                onAnalyzeTicker?(company.ticker)
                dismiss()
            }) {
                Text("Analyze")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.primaryBlue)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .cardFill()
        )
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        TrendingAnalysisDetailView(analysis: TrendingAnalysis.mockTrending[0])
    }
}
