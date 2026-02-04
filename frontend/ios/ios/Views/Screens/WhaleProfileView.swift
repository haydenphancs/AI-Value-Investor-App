//
//  WhaleProfileView.swift
//  ios
//
//  Whale Profile screen showing detailed investor information,
//  holdings, trades, and sentiment analysis.
//

import SwiftUI

// MARK: - Whale Profile View
struct WhaleProfileView: View {
    @StateObject private var viewModel: WhaleProfileViewModel
    @Environment(\.dismiss) private var dismiss

    init(whaleId: String) {
        _viewModel = StateObject(wrappedValue: WhaleProfileViewModel(whaleId: whaleId))
    }

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            if viewModel.isLoading {
                ProgressView()
                    .progressViewStyle(CircularProgressViewStyle(tint: AppColors.primaryBlue))
                    .scaleEffect(1.2)
            } else if let profile = viewModel.profile {
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xl) {
                        // Profile Header
                        WhaleProfileHeader(
                            profile: profile,
                            onFollowToggle: { viewModel.toggleFollow() }
                        )

                        // Portfolio Stats
                        WhalePortfolioStats(profile: profile)

                        // Sector Exposure
                        WhaleSectorExposureSection(sectors: profile.sectorExposure)

                        // Current Picks
                        WhaleCurrentPicksSection(
                            holdings: viewModel.displayedHoldings,
                            behaviorSummary: profile.behaviorSummary,
                            onHoldingTapped: { viewModel.viewHolding($0) },
                            onTopTenTapped: { viewModel.viewMoreHoldings() }
                        )

                        // Recent Trades
                        WhaleRecentTradesSection(
                            trades: viewModel.displayedTrades,
                            dateLabel: viewModel.tradeGroupDate,
                            onTradeTapped: { viewModel.viewTrade($0) },
                            onViewMoreTapped: { viewModel.viewMoreTrades() }
                        )

                        // Sentiment Summary
                        WhaleSentimentSummary(summary: profile.sentimentSummary)

                        // Pro Upgrade Footer
                        WhaleProUpgradeFooter()

                        // Bottom spacing
                        Spacer().frame(height: 40)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                }
                .refreshable {
                    await viewModel.refresh()
                }
            }
        }
        .navigationBarBackButtonHidden(true)
        .toolbar {
            ToolbarItem(placement: .navigationBarLeading) {
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(AppColors.textPrimary)
                }
            }

            ToolbarItem(placement: .navigationBarTrailing) {
                Button {
                    viewModel.showOptionsMenu()
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(AppColors.textPrimary)
                }
            }
        }
        .navigationDestination(item: $viewModel.selectedTickerSymbol) { ticker in
            TickerDetailView(tickerSymbol: ticker)
        }
    }
}

// MARK: - Profile Header
struct WhaleProfileHeader: View {
    let profile: WhaleProfile
    var onFollowToggle: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Avatar
            WhaleAvatarView(
                avatarURL: profile.avatarURL,
                size: 80
            )

            // Name and Title
            VStack(spacing: AppSpacing.xs) {
                Text(profile.name)
                    .font(AppTypography.title)
                    .foregroundColor(AppColors.textPrimary)

                Text(profile.title)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Follow Button
            Button {
                onFollowToggle?()
            } label: {
                HStack(spacing: AppSpacing.sm) {
                    if profile.isFollowing {
                        Image(systemName: "checkmark")
                            .font(.system(size: 14, weight: .bold))
                    }
                    Text(profile.isFollowing ? "Following" : "Follow")
                        .font(AppTypography.calloutBold)
                }
                .foregroundColor(.white)
                .padding(.horizontal, AppSpacing.xl)
                .padding(.vertical, AppSpacing.md)
                .background(AppColors.primaryBlue)
                .cornerRadius(AppCornerRadius.pill)
            }
            .buttonStyle(.plain)

            // Risk Profile Badge
            WhaleRiskBadge(riskProfile: profile.riskProfile)
        }
        .padding(.top, AppSpacing.md)
    }
}

// MARK: - Whale Avatar View
struct WhaleAvatarView: View {
    let avatarURL: String?
    let size: CGFloat

    var body: some View {
        if let url = avatarURL, let imageURL = URL(string: url) {
            AsyncImage(url: imageURL) { phase in
                switch phase {
                case .success(let image):
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .frame(width: size, height: size)
                        .clipShape(Circle())
                case .failure, .empty:
                    placeholderAvatar
                @unknown default:
                    placeholderAvatar
                }
            }
        } else {
            placeholderAvatar
        }
    }

    private var placeholderAvatar: some View {
        Circle()
            .fill(
                LinearGradient(
                    colors: [AppColors.cardBackgroundLight, AppColors.cardBackground],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .frame(width: size, height: size)
            .overlay(
                Image(systemName: "person.fill")
                    .font(.system(size: size * 0.4))
                    .foregroundColor(AppColors.textMuted)
            )
    }
}

// MARK: - Risk Badge
struct WhaleRiskBadge: View {
    let riskProfile: WhaleRiskProfile

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: riskProfile.iconName)
                .font(.system(size: 12, weight: .medium))

            Text(riskProfile.rawValue)
                .font(AppTypography.captionBold)
        }
        .foregroundColor(riskProfile.color)
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.sm)
        .background(riskProfile.color.opacity(0.15))
        .cornerRadius(AppCornerRadius.pill)
    }
}

// MARK: - Portfolio Stats
struct WhalePortfolioStats: View {
    let profile: WhaleProfile

    var body: some View {
        HStack {
            // Portfolio Value
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(profile.formattedPortfolioValue)
                    .font(.system(size: 28, weight: .bold))
                    .foregroundColor(AppColors.textPrimary)

                Text("Portfolio Value")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            Spacer()

            // YTD Return
            VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                Text(profile.formattedYTDReturn)
                    .font(.system(size: 28, weight: .bold))
                    .foregroundColor(profile.isPositiveReturn ? AppColors.bullish : AppColors.bearish)

                Text("YTD Return")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .padding(AppSpacing.lg)
    }
}

// MARK: - Sector Exposure Section
struct WhaleSectorExposureSection: View {
    let sectors: [WhaleSectorAllocation]
    @State private var showInfoSheet: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack {
                Text("Sector Exposure")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button {
                    showInfoSheet = true
                } label: {
                    Image(systemName: "info.circle")
                        .font(.system(size: 16))
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(.plain)
            }

            DonutChartView(
                segments: sectors.map { sector in
                    DonutChartSegment(
                        id: sector.id,
                        value: sector.percentage,
                        color: sector.color,
                        label: sector.name
                    )
                },
                lineWidth: 20
            )
            .padding(.vertical, AppSpacing.sm)
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
        .sheet(isPresented: $showInfoSheet) {
            SectorExposureInfoSheet()
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - Sector Exposure Info Sheet
struct SectorExposureInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // What is Sector Exposure
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "chart.pie.fill")
                                .font(.system(size: 20))
                                .foregroundColor(AppColors.primaryBlue)

                            Text("What is Sector Exposure?")
                                .font(AppTypography.title3)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("Sector exposure shows how an investor's portfolio is distributed across different market sectors. It reveals their investment strategy and risk preferences.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .lineSpacing(4)
                    }

                    Divider()
                        .background(AppColors.cardBackgroundLight)

                    // Why it Matters
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "lightbulb.fill")
                                .font(.system(size: 20))
                                .foregroundColor(AppColors.alertOrange)

                            Text("Why It Matters")
                                .font(AppTypography.title3)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            WhaleBulletPoint(
                                icon: "target",
                                text: "Understand the investor's focus areas and conviction sectors"
                            )
                            WhaleBulletPoint(
                                icon: "arrow.triangle.branch",
                                text: "See how diversified or concentrated their portfolio is"
                            )
                            WhaleBulletPoint(
                                icon: "chart.line.uptrend.xyaxis",
                                text: "Track shifts in sector allocation over time"
                            )
                            WhaleBulletPoint(
                                icon: "exclamationmark.triangle",
                                text: "Identify potential risks from sector concentration"
                            )
                        }
                    }

                    Divider()
                        .background(AppColors.cardBackgroundLight)

                    // How to Use
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "hand.tap.fill")
                                .font(.system(size: 20))
                                .foregroundColor(AppColors.bullish)

                            Text("How to Use This")
                                .font(AppTypography.title3)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("Compare the whale's sector exposure to your own portfolio. If you want to follow their strategy, consider similar sector weightings. Use this to identify sectors they're bullish on.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .lineSpacing(4)
                    }

                    Spacer().frame(height: AppSpacing.xl)
                }
                .padding(AppSpacing.xl)
            }
            .background(AppColors.background)
            .navigationTitle("Sector Exposure")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }
}

// MARK: - Whale Bullet Point
struct WhaleBulletPoint: View {
    let icon: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Image(systemName: icon)
                .font(.system(size: 14))
                .foregroundColor(AppColors.primaryBlue)
                .frame(width: 20)

            Text(text)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(2)
        }
    }
}

// MARK: - Current Picks Section
struct WhaleCurrentPicksSection: View {
    let holdings: [WhaleHolding]
    let behaviorSummary: WhaleBehaviorSummary
    var onHoldingTapped: ((WhaleHolding) -> Void)?
    var onTopTenTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            HStack {
                Text("Current Picks")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button {
                    onTopTenTapped?()
                } label: {
                    Text("Top 10")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(.plain)
            }

            // Behavior Summary Card
            WhaleBehaviorSummaryCard(behaviorSummary: behaviorSummary)

            // Holdings List
            VStack(spacing: 0) {
                ForEach(holdings) { holding in
                    WhaleHoldingRow(
                        holding: holding,
                        onTap: { onHoldingTapped?(holding) }
                    )

                    if holding.id != holdings.last?.id {
                        Divider()
                            .background(AppColors.cardBackgroundLight)
                    }
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Behavior Summary Card
struct WhaleBehaviorSummaryCard: View {
    let behaviorSummary: WhaleBehaviorSummary

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Behavior Summary:")
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textSecondary)

            Text(behaviorSummary.formattedSummary)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(AppSpacing.lg)
        .background(Color.clear)
        .cornerRadius(AppCornerRadius.medium)
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .stroke(AppColors.primaryBlue.opacity(0.3), lineWidth: 1)
        )
    }
}

// MARK: - Whale Holding Row
struct WhaleHoldingRow: View {
    let holding: WhaleHolding
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: AppSpacing.md) {
                // Logo/Ticker Icon
                WhaleTickerIcon(ticker: holding.ticker)

                // Company Info
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(holding.companyName)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)

                    Text(holding.ticker)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                // Allocation and Change
                VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                    Text(holding.formattedAllocation)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)

                    Text(holding.formattedChange)
                        .font(AppTypography.caption)
                        .foregroundColor(
                            holding.changePercent > 0 ? AppColors.bullish :
                            holding.changePercent < 0 ? AppColors.bearish :
                            AppColors.textMuted
                        )
                }
            }
            .padding(.vertical, AppSpacing.md)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Whale Ticker Icon
struct WhaleTickerIcon: View {
    let ticker: String

    private var backgroundColor: Color {
        // Generate consistent color based on ticker
        let colors: [Color] = [
            AppColors.primaryBlue,
            AppColors.bullish,
            AppColors.alertOrange,
            AppColors.alertPurple,
            AppColors.accentCyan
        ]
        let index = abs(ticker.hashValue) % colors.count
        return colors[index]
    }

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(backgroundColor.opacity(0.2))
                .frame(width: 40, height: 40)

            Text(String(ticker.prefix(1)))
                .font(.system(size: 16, weight: .bold))
                .foregroundColor(backgroundColor)
        }
    }
}

// MARK: - Recent Trades Section
struct WhaleRecentTradesSection: View {
    let trades: [WhaleTrade]
    let dateLabel: String
    var onTradeTapped: ((WhaleTrade) -> Void)?
    var onViewMoreTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            HStack {
                Text("Recent Trades")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button {
                    onViewMoreTapped?()
                } label: {
                    Text("View More")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(.plain)
            }

            // Date Label
            Text(dateLabel)
                .font(AppTypography.captionBold)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, AppSpacing.xs)

            // Trades List
            VStack(spacing: AppSpacing.md) {
                ForEach(trades) { trade in
                    WhaleTradeRow(
                        trade: trade,
                        onTap: { onTradeTapped?(trade) }
                    )
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Whale Trade Row
struct WhaleTradeRow: View {
    let trade: WhaleTrade
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: AppSpacing.md) {
                // Ticker and Company
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    HStack(spacing: AppSpacing.sm) {
                        Text(trade.ticker)
                            .font(AppTypography.bodyBold)
                            .foregroundColor(AppColors.textPrimary)

                        Text(trade.companyName)
                            .font(AppTypography.callout)
                            .foregroundColor(AppColors.textSecondary)
                            .lineLimit(1)
                    }

                    HStack(spacing: AppSpacing.sm) {
                        // Change percentage
                        Text(trade.formattedChange)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        Text("\u{2192}")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        Text(trade.formattedChange)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }
                }

                Spacer()

                // Action Badge and Amount
                VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                    WhaleTradeActionBadge(action: trade.action)

                    Text(trade.formattedAmount)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)
                }
            }
            .padding(AppSpacing.md)
            .background(AppColors.cardBackgroundLight)
            .cornerRadius(AppCornerRadius.medium)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Trade Action Badge
struct WhaleTradeActionBadge: View {
    let action: WhaleTradeAction

    var body: some View {
        Text(action.rawValue)
            .font(.system(size: 10, weight: .bold))
            .foregroundColor(.white)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xxs)
            .background(action.color)
            .cornerRadius(AppCornerRadius.small)
    }
}

// MARK: - Sentiment Summary
struct WhaleSentimentSummary: View {
    let summary: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "brain.head.profile")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(AppColors.primaryBlue)

                Text("Sentiment Summary")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(summary)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(4)
        }
        .padding(AppSpacing.lg)
        .background(
            LinearGradient(
                colors: [
                    AppColors.primaryBlue.opacity(0.15),
                    AppColors.primaryBlue.opacity(0.05)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
        .cornerRadius(AppCornerRadius.large)
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .stroke(AppColors.primaryBlue.opacity(0.3), lineWidth: 1)
        )
    }
}

// MARK: - Pro Upgrade Footer
struct WhaleProUpgradeFooter: View {
    var body: some View {
        Button {
            // Handle upgrade action
        } label: {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "lock.fill")
                    .font(.system(size: 12))

                Text("See All Holdings - Upgrade to Pro")
                    .font(AppTypography.callout)
            }
            .foregroundColor(AppColors.textSecondary)
        }
        .buttonStyle(.plain)
        .padding(.vertical, AppSpacing.lg)
    }
}

// MARK: - Preview
#Preview {
    NavigationStack {
        WhaleProfileView(whaleId: "warren-buffett")
    }
    .preferredColorScheme(.dark)
}
