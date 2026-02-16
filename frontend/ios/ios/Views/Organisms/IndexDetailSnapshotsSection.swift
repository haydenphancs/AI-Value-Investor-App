//
//  IndexDetailSnapshotsSection.swift
//  ios
//
//  Organism: Snapshots section for Index Detail — Valuation, Sector Performance, Systemic Risk
//

import SwiftUI

struct IndexDetailSnapshotsSection: View {
    let snapshotsData: IndexSnapshotsData
    var onAIAnalystTap: (() -> Void)?
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

            // Snapshot cards stacked inside one container
            VStack(spacing: 0) {
                // 1. Valuation
                ValuationSnapshotCard(valuation: snapshotsData.valuation)

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // 2. Sector Performance
                SectorPerformanceSnapshotCard(sectorPerformance: snapshotsData.sectorPerformance)

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // 3. Macro Forecast
                MacroForecastSnapshotCard(macroForecast: snapshotsData.macroForecast)
            }

            // AI Analyst button
            AIDeepResearchButton {
                onAIAnalystTap?()
            }

            // Footer: AI generation info
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "sparkles")
                    .font(.system(size: 10))
                    .foregroundColor(AppColors.textMuted)
                Text("Analysis by \(snapshotsData.generatedBy) \u{00B7} \(snapshotsData.formattedGeneratedDate)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .sheet(isPresented: $showInfoSheet) {
            SnapshotsInfoSheet()
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
    @State private var isExpanded: Bool = true

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            Button(action: {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isExpanded.toggle()
                }
            }) {
                HStack(spacing: AppSpacing.md) {
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(valuation.level.bgColor)
                            .frame(width: 36, height: 36)

                        Image(systemName: valuation.level.iconName)
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(valuation.level.color)
                    }

                    VStack(alignment: .leading, spacing: 2) {
                        Text("Valuation")
                            .font(AppTypography.calloutBold)
                            .foregroundColor(AppColors.textPrimary)

                        Text(valuation.level.rawValue)
                            .font(AppTypography.caption)
                            .foregroundColor(valuation.level.color)
                    }

                    Spacer()

                    // P/E badge
                    Text("\(String(format: "%.1f", valuation.peRatio))x P/E")
                        .font(AppTypography.footnoteBold)
                        .foregroundColor(valuation.level.color)
                        .padding(.horizontal, AppSpacing.sm)
                        .padding(.vertical, AppSpacing.xxs)
                        .background(valuation.level.bgColor)
                        .cornerRadius(AppCornerRadius.small)

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)
                }
            }
            .buttonStyle(PlainButtonStyle())

            if isExpanded {
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
                    .font(AppTypography.footnote)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, AppSpacing.md)
    }
}

// MARK: - Valuation Tier Bar
struct ValuationTierBar: View {
    let tiers: [ValuationTier]
    let gaugePosition: Double

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            // Gauge bar
            GeometryReader { geometry in
                let width = geometry.size.width
                ZStack(alignment: .leading) {
                    // Background segments
                    HStack(spacing: 2) {
                        ForEach(tiers) { tier in
                            RoundedRectangle(cornerRadius: 3)
                                .fill(tier.level.color.opacity(tier.isActive ? 0.6 : 0.15))
                                .frame(height: 8)
                        }
                    }

                    // Position indicator
                    Circle()
                        .fill(Color.white)
                        .frame(width: 14, height: 14)
                        .shadow(color: Color.black.opacity(0.3), radius: 2, x: 0, y: 1)
                        .offset(x: max(0, min(width - 14, width * gaugePosition - 7)))
                }
            }
            .frame(height: 14)

            // Labels below
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
                .font(AppTypography.footnoteBold)
                .foregroundColor(AppColors.textPrimary)
            Text(label)
                .font(.system(size: 10))
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
    @State private var isExpanded: Bool = true

    private let columns = [
        GridItem(.flexible(), spacing: AppSpacing.sm),
        GridItem(.flexible(), spacing: AppSpacing.sm)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            Button(action: {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isExpanded.toggle()
                }
            }) {
                HStack(spacing: AppSpacing.md) {
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.primaryBlue.opacity(0.15))
                            .frame(width: 36, height: 36)

                        Image(systemName: "chart.pie.fill")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(AppColors.primaryBlue)
                    }

                    VStack(alignment: .leading, spacing: 2) {
                        Text("Sector Performance")
                            .font(AppTypography.calloutBold)
                            .foregroundColor(AppColors.textPrimary)

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
                    }

                    Spacer()

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)
                }
            }
            .buttonStyle(PlainButtonStyle())

            if isExpanded {
                // Sector grid
                LazyVGrid(columns: columns, spacing: AppSpacing.sm) {
                    ForEach(sectorPerformance.sectors) { sector in
                        SectorPerformanceBlock(sector: sector)
                    }
                }

                // Story
                Text(sectorPerformance.resolvedStory)
                    .font(AppTypography.footnote)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, AppSpacing.md)
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
                .font(AppTypography.footnoteBold)
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
    @State private var isExpanded: Bool = true

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            Button(action: {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isExpanded.toggle()
                }
            }) {
                HStack(spacing: AppSpacing.md) {
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.accentCyan.opacity(0.15))
                            .frame(width: 36, height: 36)

                        Image(systemName: "globe.americas.fill")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(AppColors.accentCyan)
                    }

                    VStack(alignment: .leading, spacing: 2) {
                        Text("Macro Forecast")
                            .font(AppTypography.calloutBold)
                            .foregroundColor(AppColors.textPrimary)

                        Text("Economic Outlook")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }

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
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)
                }
            }
            .buttonStyle(PlainButtonStyle())

            if isExpanded {
                // Story
                Text(macroForecast.resolvedStory)
                    .font(AppTypography.footnote)
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
        }
        .padding(.vertical, AppSpacing.md)
    }
}

// MARK: - Macro Forecast Item Card
struct MacroForecastItemCard: View {
    let indicator: MacroForecastItem

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: indicator.signal.iconName)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(indicator.signal.color)

                Text(indicator.title)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Text(indicator.signal.rawValue)
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(indicator.signal.color)
                    .padding(.horizontal, AppSpacing.sm)
                    .padding(.vertical, 2)
                    .background(indicator.signal.bgColor)
                    .cornerRadius(AppCornerRadius.small)
            }

            Text(indicator.description)
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
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
