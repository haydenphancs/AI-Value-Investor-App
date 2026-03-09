//
//  ChartSettingsSheet.swift
//  ios
//
//  Settings sheet for chart indicators and extended hours
//

import SwiftUI

struct ChartSettingsSheet: View {
    @ObservedObject var chartSettings: ChartSettings
    let assetContext: ChartAssetContext
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {

                    // Chart type section
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Text("Chart Type")
                            .font(AppTypography.headingSmall)
                            .foregroundColor(AppColors.textPrimary)

                        HStack(spacing: AppSpacing.sm) {
                            ForEach(ChartType.allCases) { type in
                                Button {
                                    var transaction = Transaction()
                                    transaction.disablesAnimations = true
                                    withTransaction(transaction) {
                                        chartSettings.chartType = type
                                    }
                                } label: {
                                    Text(type.rawValue)
                                        .font(AppTypography.bodySmallEmphasis)
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, AppSpacing.sm)
                                        .background(
                                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                                .fill(chartSettings.chartType == type
                                                      ? AppColors.primaryBlue.opacity(0.15)
                                                      : AppColors.cardBackgroundLight.opacity(0.5))
                                        )
                                        .overlay(
                                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                                .stroke(chartSettings.chartType == type
                                                        ? AppColors.primaryBlue
                                                        : Color.clear, lineWidth: 1)
                                        )
                                        .foregroundColor(chartSettings.chartType == type
                                                         ? AppColors.primaryBlue
                                                         : AppColors.textMuted)
                                }
                                .buttonStyle(PlainButtonStyle())
                            }
                        }
                    }

                    Divider()

                    // Overlays section
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Text("Overlays")
                            .font(AppTypography.headingSmall)
                            .foregroundColor(AppColors.textPrimary)

                        ForEach(TechnicalIndicatorType.allCases.filter(\.isOverlay)) { indicator in
                            Toggle(isOn: indicatorBinding(for: indicator)) {
                                HStack(spacing: AppSpacing.sm) {
                                    Circle()
                                        .fill(indicator.defaultColor)
                                        .frame(width: 8, height: 8)
                                    Text(indicator.rawValue)
                                        .font(AppTypography.body)
                                        .foregroundColor(AppColors.textPrimary)
                                }
                            }
                            .tint(AppColors.primaryBlue)
                        }
                    }

                    Divider()

                    // Sub-charts section
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Text("Sub-charts")
                            .font(AppTypography.headingSmall)
                            .foregroundColor(AppColors.textPrimary)

                        ForEach(TechnicalIndicatorType.allCases.filter({ !$0.isOverlay })) { indicator in
                            Toggle(isOn: indicatorBinding(for: indicator)) {
                                HStack(spacing: AppSpacing.sm) {
                                    Circle()
                                        .fill(indicator.defaultColor)
                                        .frame(width: 8, height: 8)
                                    Text(indicator.rawValue)
                                        .font(AppTypography.body)
                                        .foregroundColor(AppColors.textPrimary)
                                }
                            }
                            .tint(AppColors.primaryBlue)
                        }
                    }

                    // Extended Hours — only relevant for intraday intervals
                    if assetContext.supportsExtendedHours && chartSettings.selectedInterval.isIntraday {
                        Divider()

                        Toggle(isOn: $chartSettings.showExtendedHours) {
                            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                Text("Extended Hours")
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.textPrimary)
                                Text("Show pre-market and after-hours data")
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textMuted)
                            }
                        }
                        .tint(AppColors.primaryBlue)
                    }
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Chart Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func indicatorBinding(for indicator: TechnicalIndicatorType) -> Binding<Bool> {
        Binding(
            get: { chartSettings.enabledIndicators.contains(indicator) },
            set: { enabled in
                var transaction = Transaction()
                transaction.disablesAnimations = true
                withTransaction(transaction) {
                    if enabled {
                        chartSettings.enabledIndicators.insert(indicator)
                    } else {
                        chartSettings.enabledIndicators.remove(indicator)
                    }
                }
            }
        )
    }
}
