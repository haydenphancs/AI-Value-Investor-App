//
//  FinancialSegmentedControl.swift
//  ios
//
//  Atom: Segmented control for financial tab toggles (EPS/Revenue, Annual/Quarterly, etc.)
//

import SwiftUI

struct FinancialSegmentedControl<Option: Hashable & CaseIterable & RawRepresentable>: View where Option.RawValue == String {
    @Binding var selection: Option
    let options: [Option]
    var style: SegmentStyle = .pill

    enum SegmentStyle {
        case pill      // Rounded pill buttons (EPS/Revenue)
        case compact   // Smaller, compact style (1Y/3Y)
        case toggle    // Toggle style with background
    }

    init(selection: Binding<Option>, style: SegmentStyle = .pill) {
        self._selection = selection
        self.options = Array(Option.allCases)
        self.style = style
    }

    init(selection: Binding<Option>, options: [Option], style: SegmentStyle = .pill) {
        self._selection = selection
        self.options = options
        self.style = style
    }

    var body: some View {
        HStack(spacing: style == .compact ? AppSpacing.xs : AppSpacing.sm) {
            ForEach(options, id: \.self) { option in
                segmentButton(for: option)
            }
        }
        .padding(style == .toggle ? AppSpacing.xs : 0)
        .background(
            style == .toggle
                ? RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackground)
                : nil
        )
    }

    @ViewBuilder
    private func segmentButton(for option: Option) -> some View {
        let isSelected = selection == option

        Button {
            withAnimation(.easeInOut(duration: 0.15)) {
                selection = option
            }
        } label: {
            Text(option.rawValue)
                .font(style == .compact ? AppTypography.caption : AppTypography.subheadline)
                .fontWeight(isSelected ? .semibold : .regular)
                .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
                .padding(.horizontal, horizontalPadding)
                .padding(.vertical, verticalPadding)
                .background(
                    Group {
                        if isSelected {
                            RoundedRectangle(cornerRadius: cornerRadius)
                                .fill(AppColors.primaryBlue)
                        } else if style != .toggle {
                            RoundedRectangle(cornerRadius: cornerRadius)
                                .fill(AppColors.cardBackground)
                        }
                    }
                )
        }
        .buttonStyle(PlainButtonStyle())
    }

    private var horizontalPadding: CGFloat {
        switch style {
        case .pill: return AppSpacing.md
        case .compact: return AppSpacing.sm
        case .toggle: return AppSpacing.md
        }
    }

    private var verticalPadding: CGFloat {
        switch style {
        case .pill: return AppSpacing.sm
        case .compact: return AppSpacing.xs
        case .toggle: return AppSpacing.sm
        }
    }

    private var cornerRadius: CGFloat {
        switch style {
        case .pill: return AppCornerRadius.pill
        case .compact: return AppCornerRadius.small
        case .toggle: return AppCornerRadius.small
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: 24) {
            // Pill style
            FinancialSegmentedControl(
                selection: .constant(EarningsMetricType.eps),
                style: .pill
            )

            // Compact style
            FinancialSegmentedControl(
                selection: .constant(EarningsTimePeriod.oneYear),
                style: .compact
            )

            // Toggle style
            FinancialSegmentedControl(
                selection: .constant(GrowthPeriodType.annual),
                style: .toggle
            )
        }
    }
}
