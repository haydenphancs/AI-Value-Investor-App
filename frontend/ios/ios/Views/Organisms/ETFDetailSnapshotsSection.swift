//
//  ETFDetailSnapshotsSection.swift
//  ios
//
//  Organism: Snapshots section for ETF Detail with rich expandable cards
//  Categories: Identity & Rating, Strategy, Net Yield, Holdings & Risk
//

import SwiftUI

struct ETFDetailSnapshotsSection: View {
    let etfData: ETFDetailData
    var onDeepResearchTap: (() -> Void)?
    @State private var showInfoSheet: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title with info button
            HStack {
                Text("Snapshots")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button(action: {
                    showInfoSheet = true
                }) {
                    Text("What's Snapshots?")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())
            }

            // Snapshot cards
            VStack(spacing: 0) {
                ETFIdentityRatingCard(etfData: etfData)
                ETFStrategyCard(strategy: etfData.strategy)
                ETFNetYieldCard(netYield: etfData.netYield, symbol: etfData.symbol)
                ETFHoldingsRiskCard(holdingsRisk: etfData.holdingsRisk)
            }

            // AI Deep Research button
            AIDeepResearchButton {
                onDeepResearchTap?()
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .sheet(isPresented: $showInfoSheet) {
            ETFSnapshotsInfoSheet()
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - Snapshot Card Header (shared expandable header)

struct ETFSnapshotCardHeader: View {
    let category: ETFSnapshotCategory
    let isExpanded: Bool
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.md) {
                ZStack {
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .fill(category.iconColor.opacity(0.15))
                        .frame(width: 36, height: 36)

                    Image(systemName: category.iconName)
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(category.iconColor)
                }

                Text(category.rawValue)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(.vertical, AppSpacing.md)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

// MARK: - 1. Identity & Rating Card

struct ETFIdentityRatingCard: View {
    let etfData: ETFDetailData
    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ETFSnapshotCardHeader(
                category: .identityAndRating,
                isExpanded: isExpanded,
                onTap: {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        isExpanded.toggle()
                    }
                }
            )

            if isExpanded {
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    // Top row: Symbol + Name | Price + Change
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                            Text(etfData.symbol)
                                .font(.system(size: 28, weight: .bold))
                                .foregroundColor(AppColors.textPrimary)
                            Text(etfData.name)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                                .lineLimit(2)
                        }

                        Spacer()

                        VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                            Text(etfData.formattedPrice)
                                .font(.system(size: 22, weight: .bold))
                                .foregroundColor(AppColors.textPrimary)

                            // Change pill
                            Text(etfData.formattedChangePill)
                                .font(AppTypography.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(.white)
                                .padding(.horizontal, AppSpacing.sm)
                                .padding(.vertical, AppSpacing.xxs)
                                .background(
                                    Capsule()
                                        .fill(etfData.isPositive ? AppColors.bullish : AppColors.bearish)
                                )
                        }
                    }

                    // Badges row
                    HStack(spacing: AppSpacing.sm) {
                        ETFSnapshotBadge(
                            text: "Score: \(etfData.identityRating.score)/\(etfData.identityRating.maxScore)",
                            color: AppColors.primaryBlue
                        )
                        ETFSnapshotBadge(
                            text: "ESG: \(etfData.identityRating.esgRating)",
                            color: AppColors.bullish
                        )
                        ETFSnapshotBadge(
                            text: etfData.identityRating.volatilityLabel,
                            color: AppColors.accentCyan
                        )
                    }
                }
                .padding(.bottom, AppSpacing.md)
            }

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
    }
}

// MARK: - 2. Strategy Card

struct ETFStrategyCard: View {
    let strategy: ETFStrategy
    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ETFSnapshotCardHeader(
                category: .strategy,
                isExpanded: isExpanded,
                onTap: {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        isExpanded.toggle()
                    }
                }
            )

            if isExpanded {
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    // The hook
                    Text("\"\(strategy.hook)\"")
                        .font(AppTypography.footnote)
                        .foregroundColor(AppColors.textSecondary)
                        .italic()
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)

                    // Tags
                    HStack(spacing: AppSpacing.sm) {
                        ForEach(strategy.tags, id: \.self) { tag in
                            ETFSnapshotTag(text: tag)
                        }
                    }
                }
                .padding(.bottom, AppSpacing.md)
            }

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
    }
}

// MARK: - 3. Net Yield Card

struct ETFNetYieldCard: View {
    let netYield: ETFNetYield
    let symbol: String
    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ETFSnapshotCardHeader(
                category: .netYield,
                isExpanded: isExpanded,
                onTap: {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        isExpanded.toggle()
                    }
                }
            )

            if isExpanded {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    // Cost vs Dividend side-by-side (equal height)
                    HStack(alignment: .top, spacing: AppSpacing.md) {
                        // Cost side
                        VStack(alignment: .leading, spacing: AppSpacing.sm) {
                            Text("Cost")
                                .font(AppTypography.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(AppColors.bearish)

                            Text("Fee: \(netYield.formattedExpenseRatio)")
                                .font(AppTypography.calloutBold)
                                .foregroundColor(AppColors.textPrimary)

                            Text(netYield.feeContext)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                                .fixedSize(horizontal: false, vertical: true)

                            Spacer(minLength: 0)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                        .padding(AppSpacing.md)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                .fill(AppColors.bearish.opacity(0.08))
                        )

                        // Dividend side
                        VStack(alignment: .leading, spacing: AppSpacing.sm) {
                            Text("Dividend")
                                .font(AppTypography.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(AppColors.bullish)

                            Text("Yield: \(netYield.formattedDividendYield)")
                                .font(AppTypography.calloutBold)
                                .foregroundColor(AppColors.textPrimary)

                            Text("Pays \(netYield.payFrequency)")
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)

                            Text(netYield.yieldContext)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                                .fixedSize(horizontal: false, vertical: true)

                            Spacer(minLength: 0)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                        .padding(AppSpacing.md)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                .fill(AppColors.bullish.opacity(0.08))
                        )
                    }
                    .fixedSize(horizontal: false, vertical: true)

                    // The verdict
                    HStack(alignment: .top, spacing: AppSpacing.sm) {
                        Image(systemName: "lightbulb.fill")
                            .font(.system(size: 14))
                            .foregroundColor(AppColors.neutral)

                        Text(netYield.verdict)
                            .font(AppTypography.footnote)
                            .fontWeight(.medium)
                            .foregroundColor(AppColors.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(AppSpacing.md)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.neutral.opacity(0.1))
                    )

                    // Dividend History row
                    ETFDividendHistoryRow(
                        lastPayment: netYield.lastDividendPayment,
                        symbol: symbol,
                        dividendHistory: netYield.dividendHistory
                    )
                }
                .padding(.bottom, AppSpacing.md)
            }

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
    }
}

// MARK: - Dividend History Row

struct ETFDividendHistoryRow: View {
    let lastPayment: ETFDividendPayment
    let symbol: String
    let dividendHistory: [ETFDividendPayment]
    @State private var showDividendHistory = false

    var body: some View {
        Button(action: {
            showDividendHistory = true
        }) {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Title row with chevron
                HStack {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "calendar.badge.clock")
                            .font(.system(size: 14))
                            .foregroundColor(AppColors.primaryBlue)

                        Text("Dividend History")
                            .font(AppTypography.footnote)
                            .fontWeight(.semibold)
                            .foregroundColor(AppColors.textPrimary)
                    }

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)
                }

                // Last payment details
                HStack(spacing: 0) {
                    // Dividend Per Share
                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text("Per Share")
                            .font(.system(size: 10))
                            .foregroundColor(AppColors.textMuted)
                        Text(lastPayment.dividendPerShare)
                            .font(AppTypography.caption)
                            .fontWeight(.semibold)
                            .foregroundColor(AppColors.bullish)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    // Ex-Dividend Date
                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text("Ex-Div Date")
                            .font(.system(size: 10))
                            .foregroundColor(AppColors.textMuted)
                        Text(lastPayment.exDividendDate)
                            .font(AppTypography.caption)
                            .fontWeight(.medium)
                            .foregroundColor(AppColors.textSecondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    // Pay Date
                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text("Pay Date")
                            .font(.system(size: 10))
                            .foregroundColor(AppColors.textMuted)
                        Text(lastPayment.payDate)
                            .font(AppTypography.caption)
                            .fontWeight(.medium)
                            .foregroundColor(AppColors.textSecondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(AppSpacing.md)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackgroundLight.opacity(0.5))
            )
            .overlay(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .stroke(AppColors.cardBackgroundLight, lineWidth: 1)
            )
        }
        .buttonStyle(PlainButtonStyle())
        .sheet(isPresented: $showDividendHistory) {
            ETFDividendHistoryView(
                symbol: symbol,
                dividendHistory: dividendHistory
            )
        }
    }
}

// MARK: - 4. Holdings & Risk Card

struct ETFHoldingsRiskCard: View {
    let holdingsRisk: ETFHoldingsRisk
    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ETFSnapshotCardHeader(
                category: .holdingsAndRisk,
                isExpanded: isExpanded,
                onTap: {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        isExpanded.toggle()
                    }
                }
            )

            if isExpanded {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    // Asset Allocation
                    ETFAssetAllocationBar(allocation: holdingsRisk.assetAllocation)

                    // Top Sectors
                    ETFSectorsView(sectors: holdingsRisk.topSectors)

                    // Top Holdings (scrollable row)
                    ETFTopHoldingsRow(holdings: holdingsRisk.topHoldings)

                    // Concentration Meter
                    ETFConcentrationMeter(concentration: holdingsRisk.concentration)
                }
                .padding(.bottom, AppSpacing.md)
            }

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
    }
}

// MARK: - Asset Allocation Stacked Bar

struct ETFAssetAllocationBar: View {
    let allocation: ETFAssetAllocation

    private var segments: [(label: String, value: Double, color: Color)] {
        [
            ("Stocks", allocation.equities, AppColors.bullish),
            ("Bonds", allocation.bonds, AppColors.primaryBlue),
            ("Crypto", allocation.crypto, Color.purple),
            ("Cash", allocation.cash, AppColors.textMuted)
        ].filter { $0.value > 0 }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Title with total assets
            HStack {
                Text("Asset Allocation")
                    .font(AppTypography.footnote)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Text(allocation.totalAssets)
                    .font(AppTypography.footnote)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Stacked bar
            GeometryReader { geometry in
                HStack(spacing: 1) {
                    ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                        let width = (segment.value / 100.0) * geometry.size.width
                        RoundedRectangle(cornerRadius: 2)
                            .fill(segment.color)
                            .frame(width: max(width, 2))
                    }
                }
            }
            .frame(height: 8)
            .clipShape(Capsule())

            // Legend
            HStack(spacing: AppSpacing.md) {
                ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                    HStack(spacing: AppSpacing.xxs) {
                        Circle()
                            .fill(segment.color)
                            .frame(width: 8, height: 8)
                        Text("\(segment.label) \(String(format: "%.1f", segment.value))%")
                            .font(.system(size: 10))
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            }
        }
    }
}

// MARK: - Sectors View

struct ETFSectorsView: View {
    let sectors: [ETFSectorWeight]

    // "Others" = 100% minus the sum of the visible sectors
    private var othersWeight: Double {
        let sum = sectors.reduce(0) { $0 + $1.weight }
        return max(100.0 - sum, 0)
    }

    private var allRows: [(name: String, weight: Double, isOther: Bool)] {
        var rows: [(name: String, weight: Double, isOther: Bool)] = sectors.map {
            ($0.name, $0.weight, false)
        }
        if othersWeight > 0.1 {
            rows.append(("Others", othersWeight, true))
        }
        return rows
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Sectors")
                .font(AppTypography.footnote)
                .fontWeight(.semibold)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.xs) {
                ForEach(Array(allRows.enumerated()), id: \.offset) { _, row in
                    HStack(spacing: AppSpacing.sm) {
                        Text(row.name)
                            .font(AppTypography.caption)
                            .foregroundColor(row.isOther ? AppColors.textMuted : AppColors.textSecondary)
                            .frame(width: 110, alignment: .leading)

                        // Bar scaled to 100% of portfolio
                        GeometryReader { geometry in
                            let barWidth = (row.weight / 100.0) * geometry.size.width
                            ZStack(alignment: .leading) {
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(AppColors.cardBackgroundLight)
                                    .frame(height: 6)

                                RoundedRectangle(cornerRadius: 3)
                                    .fill(row.isOther ? AppColors.textMuted.opacity(0.5) : AppColors.primaryBlue.opacity(0.7))
                                    .frame(width: max(barWidth, 4), height: 6)
                            }
                        }
                        .frame(height: 6)

                        Text(String(format: "%.1f%%", row.weight))
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(row.isOther ? AppColors.textMuted : AppColors.textPrimary)
                            .frame(width: 44, alignment: .trailing)
                    }
                }
            }
        }
    }
}

// MARK: - Top Holdings Scrollable Row

struct ETFTopHoldingsRow: View {
    let holdings: [ETFTopHolding]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("The Ingredients")
                .font(AppTypography.footnote)
                .fontWeight(.semibold)
                .foregroundColor(AppColors.textPrimary)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.sm) {
                    ForEach(holdings) { holding in
                        ETFHoldingSquare(holding: holding)
                    }
                }
            }
        }
    }
}

struct ETFHoldingSquare: View {
    let holding: ETFTopHolding

    var body: some View {
        VStack(spacing: AppSpacing.xs) {
            // Logo placeholder
            ZStack {
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 48, height: 48)

                Text(String(holding.symbol.prefix(2)))
                    .font(.system(size: 16, weight: .bold))
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(holding.symbol)
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(AppColors.textPrimary)

            Text(holding.formattedWeight)
                .font(.system(size: 10))
                .foregroundColor(AppColors.textMuted)
        }
        .frame(width: 60)
    }
}

// MARK: - Concentration Meter

struct ETFConcentrationMeter: View {
    let concentration: ETFConcentration

    // Normalized position (0 to 1) for the meter
    private var meterPosition: Double {
        min(max(concentration.weight / 50.0, 0), 1)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Label row
            HStack {
                Text("Top \(concentration.topN) Weight: \(concentration.formattedWeight)")
                    .font(AppTypography.footnote)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Text(concentration.level.label)
                    .font(AppTypography.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(concentration.level.color)
            }

            // Meter bar
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    // Background gradient (green -> yellow -> red)
                    LinearGradient(
                        colors: [AppColors.bullish, AppColors.neutral, AppColors.bearish],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                    .frame(height: 8)
                    .clipShape(Capsule())
                    .opacity(0.3)

                    // Filled portion
                    LinearGradient(
                        colors: [AppColors.bullish, concentration.level.color],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                    .frame(width: meterPosition * geometry.size.width, height: 8)
                    .clipShape(Capsule())

                    // Indicator
                    Circle()
                        .fill(Color.white)
                        .frame(width: 14, height: 14)
                        .shadow(color: Color.black.opacity(0.3), radius: 2, x: 0, y: 1)
                        .offset(x: (meterPosition * geometry.size.width) - 7)
                }
            }
            .frame(height: 14)

            // Insight
            HStack(alignment: .top, spacing: AppSpacing.sm) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 12))
                    .foregroundColor(concentration.level.color)

                Text(concentration.insight)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: - Reusable Badge & Tag Components

struct ETFSnapshotBadge: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .semibold))
            .foregroundColor(color)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xxs)
            .background(
                Capsule()
                    .fill(color.opacity(0.15))
            )
            .overlay(
                Capsule()
                    .stroke(color.opacity(0.3), lineWidth: 1)
            )
    }
}

struct ETFSnapshotTag: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .foregroundColor(AppColors.textSecondary)
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.xs)
            .background(
                Capsule()
                    .fill(AppColors.cardBackgroundLight)
            )
    }
}

// MARK: - ETF Snapshots Info Sheet

struct ETFSnapshotsInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // What are Snapshots?
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "camera.viewfinder")
                                .font(.system(size: 18))
                                .foregroundColor(AppColors.neutral)
                            Text("What are Snapshots?")
                                .font(AppTypography.headline)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("Snapshots provide a quick, comprehensive view of an ETF's key dimensions. Each snapshot covers a different aspect of the fund, giving you an instant understanding of its identity, strategy, yield, and risk profile.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    // Snapshot Categories
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "square.grid.2x2.fill")
                                .font(.system(size: 18))
                                .foregroundColor(AppColors.neutral)
                            Text("Snapshot Categories")
                                .font(AppTypography.headline)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            SnapshotBulletPoint(
                                icon: "shield.checkered",
                                title: "Identity & Rating",
                                description: "Who is this fund? The issuer, quality score, ESG rating, and volatility classification at a glance."
                            )

                            SnapshotBulletPoint(
                                icon: "scope",
                                title: "Strategy",
                                description: "What does this fund do? A plain-English explanation of the fund's goal and investment approach."
                            )

                            SnapshotBulletPoint(
                                icon: "percent",
                                title: "Net Yield",
                                description: "What you pay vs. what you earn. Expense ratio, dividend yield, and the bottom-line verdict."
                            )

                            SnapshotBulletPoint(
                                icon: "chart.bar.doc.horizontal.fill",
                                title: "Holdings & Risk",
                                description: "What's inside the box? Asset allocation, sector exposure, top holdings, and concentration risk."
                            )
                        }
                    }

                    // Pro Tips
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "sparkles")
                                .font(.system(size: 18))
                                .foregroundColor(AppColors.neutral)
                            Text("Pro Tips")
                                .font(AppTypography.headline)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            ProTipCard(
                                icon: "arrow.triangle.2.circlepath",
                                tip: "Compare expense ratios across similar ETFs. Even small differences compound significantly over long holding periods."
                            )

                            ProTipCard(
                                icon: "chart.pie.fill",
                                tip: "Check top holdings concentration. An ETF with 40% in its top 10 holdings behaves differently than one evenly spread across 500."
                            )

                            ProTipCard(
                                icon: "shield.fill",
                                tip: "Always review Holdings & Risk before investing. Understand what you actually own inside the fund."
                            )
                        }
                    }
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("About Snapshots")
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
        .preferredColorScheme(.dark)
    }
}

// MARK: - Previews

#Preview {
    ScrollView {
        ETFDetailSnapshotsSection(etfData: ETFDetailData.sampleSPY)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}

#Preview("Info Sheet") {
    ETFSnapshotsInfoSheet()
}
