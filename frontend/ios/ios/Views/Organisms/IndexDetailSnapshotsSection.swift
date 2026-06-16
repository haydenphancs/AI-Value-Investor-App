//
//  IndexDetailSnapshotsSection.swift
//  ios
//
//  Organism: Unified Snapshots section for Index Detail
//  Combines Valuation, Sector Performance, and Macro Forecast
//  in a single container matching the Ticker Detail pattern.
//

import SwiftUI

struct IndexDetailSnapshotsSection: View {
    let snapshotsData: IndexSnapshotsData
    var onAIAnalystTap: (() -> Void)?
    @State private var showInfoSheet: Bool = false

    private var formattedDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MM/dd/yyyy"
        formatter.timeZone = TimeZone(identifier: "America/New_York")
        return formatter.string(from: snapshotsData.generatedDate)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section header
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                HStack {
                    Text("Market Snapshots")
                        .font(AppTypography.heading)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    Button(action: { showInfoSheet = true }) {
                        Text("What's this?")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }
                    .buttonStyle(PlainButtonStyle())
                }

                Text("For all indices")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                Text("Updated on \(formattedDate) ET")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            // Expandable snapshot cards
            VStack(spacing: 0) {
                ValuationSnapshotCard(valuation: snapshotsData.valuation)
                SectorPerformanceSnapshotCard(sectorPerformance: snapshotsData.sectorPerformance)
                MacroForecastSnapshotCard(macroForecast: snapshotsData.macroForecast)
            }

            // AI Analyst button
            AIDeepResearchButton {
                onAIAnalystTap?()
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .sheet(isPresented: $showInfoSheet) {
            IndexSnapshotsInfoSheet()
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - ──────────────────────────────────────────────
// MARK:   1. VALUATION SNAPSHOT CARD
// MARK: - ──────────────────────────────────────────────

struct ValuationSnapshotCard: View {
    let valuation: IndexValuationSnapshot
    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header
            Button(action: {
                withAnimation(.easeInOut(duration: 0.25)) {
                    isExpanded.toggle()
                }
            }) {
                HStack(spacing: AppSpacing.md) {
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(valuation.level.bgColor)
                            .frame(width: 36, height: 36)

                        Image(systemName: valuation.level.iconName)
                            .font(AppTypography.iconDefault).fontWeight(.semibold)
                            .foregroundColor(valuation.level.color)
                    }

                    Text("Valuation")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    // Valuation level badge
                    Text(valuation.level.rawValue)
                        .font(AppTypography.caption)
                        .foregroundColor(valuation.level.color)
                        .padding(.horizontal, AppSpacing.sm)
                        .padding(.vertical, AppSpacing.xxs)
                        .background(valuation.level.bgColor)
                        .cornerRadius(AppCornerRadius.small)

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(AppTypography.iconXS).fontWeight(.semibold)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.vertical, AppSpacing.md)
            }
            .buttonStyle(PlainButtonStyle())

            if isExpanded {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    // Tier bar
                    ValuationTierBar(tiers: valuation.tiers, gaugePosition: valuation.gaugePosition)

                    // Key metrics row
                    HStack(spacing: 0) {
                        ValuationMetricPill(label: "P/E (TTM)", value: String(format: "%.1fx", valuation.peRatio))
                        Spacer()
                        ValuationMetricPill(label: "Fwd P/E", value: String(format: "%.1fx", valuation.forwardPE))
                        Spacer()
                        ValuationMetricPill(label: "10Y Avg", value: String(format: "%.0fx", valuation.historicalAvgPE))
                    }

                    // Story
                    Text(valuation.resolvedStory)
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.bottom, AppSpacing.md)
                .transition(.opacity)
            }

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
    }
}

// MARK: - Valuation Tier Bar
struct ValuationTierBar: View {
    let tiers: [ValuationTier]
    let gaugePosition: Double

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            GeometryReader { geometry in
                let width = geometry.size.width
                ZStack(alignment: .leading) {
                    HStack(spacing: 2) {
                        ForEach(tiers) { tier in
                            RoundedRectangle(cornerRadius: 3)
                                .fill(tier.level.color.opacity(tier.isActive ? 0.6 : 0.15))
                                .frame(height: 8)
                        }
                    }

                    Circle()
                        .fill(Color.white)
                        .frame(width: 14, height: 14)
                        .shadow(color: Color.black.opacity(0.3), radius: 2, x: 0, y: 1)
                        .offset(x: max(0, min(width - 14, width * gaugePosition - 7)))
                }
            }
            .frame(height: 14)

            HStack(spacing: 2) {
                ForEach(tiers) { tier in
                    VStack(spacing: 2) {
                        Text(tier.level.rawValue)
                            .font(.system(size: 9, weight: tier.isActive ? .bold : .regular))
                            .foregroundColor(tier.isActive ? tier.level.color : AppColors.textMuted)
                        Text(tier.rangeLabel)
                            .font(.system(size: 8))
                            .foregroundColor(AppColors.textMuted)
                    }
                    .frame(maxWidth: .infinity)
                }
            }
        }
    }
}

// MARK: - Valuation Metric Pill
struct ValuationMetricPill: View {
    let label: String
    let value: String

    var body: some View {
        VStack(spacing: 2) {
            Text(value)
                .font(AppTypography.labelSmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
            Text(label)
                .font(AppTypography.captionSmall)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.sm)
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
    }
}

// MARK: - ──────────────────────────────────────────────
// MARK:   2. SECTOR PERFORMANCE SNAPSHOT CARD
// MARK: - ──────────────────────────────────────────────

struct SectorPerformanceSnapshotCard: View {
    let sectorPerformance: IndexSectorPerformanceSnapshot
    @State private var isExpanded: Bool = false

    private let columns = [
        GridItem(.flexible(), spacing: AppSpacing.sm),
        GridItem(.flexible(), spacing: AppSpacing.sm)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header
            Button(action: {
                withAnimation(.easeInOut(duration: 0.25)) {
                    isExpanded.toggle()
                }
            }) {
                HStack(spacing: AppSpacing.md) {
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.primaryBlue.opacity(0.15))
                            .frame(width: 36, height: 36)

                        Image(systemName: "chart.pie.fill")
                            .font(AppTypography.iconDefault).fontWeight(.semibold)
                            .foregroundColor(AppColors.primaryBlue)
                    }

                    Text("Sector Performance")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    // Advancing/declining badge
                    HStack(spacing: AppSpacing.xs) {
                        Text("\(sectorPerformance.advancingSectors) up")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.bullish)
                        Text("\u{00B7}")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                        Text("\(sectorPerformance.decliningSectors) down")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.bearish)
                    }
                    .padding(.horizontal, AppSpacing.sm)
                    .padding(.vertical, AppSpacing.xxs)
                    .background(AppColors.primaryBlue.opacity(0.15))
                    .cornerRadius(AppCornerRadius.small)

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(AppTypography.iconXS).fontWeight(.semibold)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.vertical, AppSpacing.md)
            }
            .buttonStyle(PlainButtonStyle())

            if isExpanded {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    // Sector grid
                    LazyVGrid(columns: columns, spacing: AppSpacing.sm) {
                        ForEach(sectorPerformance.sectors) { sector in
                            SectorPerformanceBlock(sector: sector)
                        }
                    }

                    // Story
                    Text(sectorPerformance.resolvedStory)
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.bottom, AppSpacing.md)
                .transition(.opacity)
            }

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
    }
}

// MARK: - Sector Performance Block
struct SectorPerformanceBlock: View {
    let sector: SectorPerformanceEntry

    var body: some View {
        HStack {
            Text(sector.sector)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)

            Spacer()

            Text(sector.formattedChange)
                .font(AppTypography.labelSmallEmphasis)
                .foregroundColor(sector.color)
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.sm)
        .background(sector.bgColor)
        .cornerRadius(AppCornerRadius.medium)
    }
}

// MARK: - ──────────────────────────────────────────────
// MARK:   3. MACRO FORECAST SNAPSHOT CARD
// MARK: - ──────────────────────────────────────────────

struct MacroForecastSnapshotCard: View {
    let macroForecast: IndexMacroForecastSnapshot
    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header
            Button(action: {
                withAnimation(.easeInOut(duration: 0.25)) {
                    isExpanded.toggle()
                }
            }) {
                HStack(spacing: AppSpacing.md) {
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.accentCyan.opacity(0.15))
                            .frame(width: 36, height: 36)

                        Image(systemName: "globe.americas.fill")
                            .font(AppTypography.iconDefault).fontWeight(.semibold)
                            .foregroundColor(AppColors.accentCyan)
                    }

                    Text("Economic Outlook")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    // Indicator count badge
                    Text("\(macroForecast.indicators.count) indicators")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.accentCyan)
                        .padding(.horizontal, AppSpacing.sm)
                        .padding(.vertical, AppSpacing.xxs)
                        .background(AppColors.accentCyan.opacity(0.15))
                        .cornerRadius(AppCornerRadius.small)

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(AppTypography.iconXS).fontWeight(.semibold)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.vertical, AppSpacing.md)
            }
            .buttonStyle(PlainButtonStyle())

            if isExpanded {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    // Story
                    Text(macroForecast.resolvedStory)
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)

                    // Indicator items
                    VStack(spacing: AppSpacing.sm) {
                        ForEach(macroForecast.indicators) { indicator in
                            MacroForecastItemCard(indicator: indicator)
                        }
                    }
                }
                .padding(.bottom, AppSpacing.md)
                .transition(.opacity)
            }

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
    }
}

// MARK: - Macro Forecast Item Card
struct MacroForecastItemCard: View {
    let indicator: MacroForecastItem

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: indicator.signal.iconName)
                    .font(AppTypography.iconSmall).fontWeight(.semibold)
                    .foregroundColor(indicator.signal.color)

                Text(indicator.title)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Text(indicator.signal.rawValue)
                    .font(AppTypography.captionSmallEmphasis)
                    .foregroundColor(indicator.signal.color)
                    .padding(.horizontal, AppSpacing.sm)
                    .padding(.vertical, 2)
                    .background(indicator.signal.bgColor)
                    .cornerRadius(AppCornerRadius.small)
            }

            Text(indicator.description)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
    }
}

// MARK: - ──────────────────────────────────────────────
// MARK:   INDEX SNAPSHOTS INFO SHEET
// MARK: - ──────────────────────────────────────────────

struct IndexSnapshotsInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // What are Snapshots?
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "camera.viewfinder")
                                .font(AppTypography.iconMedium)
                                .foregroundColor(AppColors.neutral)
                            Text("What are Index Snapshots?")
                                .font(AppTypography.headingSmall)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("Index Snapshots provide a quick, comprehensive view of the overall market health. Each snapshot evaluates a different dimension — from valuation levels and sector breadth to macroeconomic indicators — giving you an instant read on market conditions.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    // Snapshot Categories
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "square.stack.3d.up.fill")
                                .font(AppTypography.iconMedium)
                                .foregroundColor(AppColors.neutral)
                            Text("Snapshot Categories")
                                .font(AppTypography.headingSmall)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            SnapshotBulletPoint(
                                icon: "tag.fill",
                                title: "Valuation",
                                description: "Compares the index P/E ratio against historical averages to gauge whether the market is cheap or expensive."
                            )

                            SnapshotBulletPoint(
                                icon: "chart.pie.fill",
                                title: "Sector Performance",
                                description: "Shows which sectors are leading or lagging, helping you spot rotation trends and opportunities."
                            )

                            SnapshotBulletPoint(
                                icon: "globe.americas.fill",
                                title: "Macro Forecast",
                                description: "AI-analyzed macroeconomic indicators including GDP, inflation, labor markets, and geopolitical risks."
                            )
                        }
                    }

                    // Pro Tips
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "sparkles")
                                .font(AppTypography.iconMedium)
                                .foregroundColor(AppColors.neutral)
                            Text("Pro Tips")
                                .font(AppTypography.headingSmall)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            ProTipCard(
                                icon: "arrow.triangle.2.circlepath",
                                tip: "Use sector performance to identify rotation. Money flowing out of defensive sectors into growth sectors often signals bullish sentiment."
                            )

                            ProTipCard(
                                icon: "calendar",
                                tip: "Snapshots are refreshed with AI analysis. Check the updated date to ensure you're viewing the latest market read."
                            )

                            ProTipCard(
                                icon: "chart.bar.fill",
                                tip: "A market trading below its historical P/E average may present value opportunities, but always consider the macro backdrop."
                            )
                        }
                    }
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("About Market Snapshots")
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

// MARK: - Preview

#Preview {
    ScrollView {
        IndexDetailSnapshotsSection(snapshotsData: IndexSnapshotsData.sampleData)
            .padding(.horizontal, AppSpacing.lg)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
