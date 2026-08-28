//
//  DetailHeaderChartSkeleton.swift
//  ios
//
//  Molecule: instant-render placeholder for the price-header + chart region of the
//  asset detail screens, shown while the first data loads — replacing the old
//  full-screen blocking spinner (which also intercepted the back tap). Reuses the
//  ShimmerEffect atom. The chart block matches the real chart's height footprint so
//  there is no layout jump when live data swaps in.
//
//  WHY THERE IS A CAPTION: a shimmer on its own is a weak signal. A TestFlight tester
//  sat through a 5-7s cold open on AVGO and reported that the screen said nothing at
//  all — and to VoiceOver that was literally true, since this view carried no
//  accessibility modifier of any kind. The caption names what is loading, and escalates
//  after `slowThresholdSeconds` so a genuinely slow load reads as "still working"
//  rather than "stuck".
//

import SwiftUI

struct DetailHeaderChartSkeleton: View {

    /// Named in the caption ("Loading AVGO…"). `nil` or blank → the generic "Loading…",
    /// so a screen that has not resolved its symbol yet degrades instead of printing
    /// "Loading …".
    var symbol: String? = nil

    /// Seconds before the caption escalates. A parameter only so the preview can show
    /// the escalated state; every screen uses the default.
    var slowThresholdSeconds: Double = 2.5

    @State private var isSlow = false

    private var bar: some View {
        RoundedRectangle(cornerRadius: 6, style: .continuous)
            .fill(AppColors.cardBackgroundLight)
    }

    /// Single source of truth for the visible caption AND the VoiceOver label, so the
    /// two can never drift.
    private var message: String {
        if isSlow {
            return "Still loading — this is taking longer than usual"
        }
        guard let symbol, !symbol.trimmingCharacters(in: .whitespaces).isEmpty else {
            return "Loading…"
        }
        return "Loading \(symbol)…"
    }

    // The caption deliberately sits OUTSIDE the `.shimmer()`ed group: ShimmerModifier
    // sweeps an opacity gradient and ends with `.clipped()`, so text placed inside it
    // would pulse and clip.
    private var caption: some View {
        HStack(spacing: AppSpacing.sm) {
            ProgressView()
                .progressViewStyle(.circular)
                .tint(AppColors.textSecondary)
                .scaleEffect(0.8)

            Text(message)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                // Wrap rather than truncate at the largest Dynamic Type sizes — the
                // escalated string is long, and a clipped "this is taking longer tha…"
                // is worse than two lines.
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var placeholders: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Price-header placeholders (price / change). There is deliberately NO
            // company-name bar: the caption above occupies that slot — which is both
            // where the company name will actually appear, and what keeps the skeleton
            // the same height as the real header. Preserving that no-layout-jump
            // property is why the caption replaces a bar instead of stacking above one.
            VStack(alignment: .leading, spacing: 8) {
                bar.frame(width: 120, height: 30)
                bar.frame(width: 90, height: 14)
            }

            // Chart placeholder (same height footprint as TickerChartView).
            RoundedRectangle(cornerRadius: AppCornerRadius.medium, style: .continuous)
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 180)
                .padding(.top, AppSpacing.sm)

            // Range-selector pills.
            HStack(spacing: 8) {
                ForEach(0..<7, id: \.self) { _ in
                    Capsule()
                        .fill(AppColors.cardBackgroundLight)
                        .frame(width: 34, height: 24)
                }
            }
        }
        .shimmer()
    }

    var body: some View {
        // `spacing: 8` matches the gap the placeholder bars use among themselves, so the
        // caption reads as the header's first row rather than as a banner above it.
        VStack(alignment: .leading, spacing: 8) {
            caption
            placeholders
        }
        .padding(.top, AppSpacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, AppSpacing.lg)
        // One element with one label: without `children: .ignore` VoiceOver still walks
        // the individual shimmer bars (the mistake DetailTabSkeleton made).
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(message)
        .task {
            // `.task` is cancelled the instant real data replaces this view, so a fast
            // load never escalates. NOTE the `do/catch` rather than `try?`: `try?`
            // swallows the CancellationError but execution CONTINUES to the next line,
            // which would flip `isSlow` on a view that is already going away.
            do {
                try await Task.sleep(for: .seconds(slowThresholdSeconds))
            } catch {
                return
            }
            withAnimation(.easeInOut(duration: 0.2)) { isSlow = true }
        }
    }
}

#Preview("Loading") {
    ZStack {
        AppColors.background.ignoresSafeArea()
        DetailHeaderChartSkeleton(symbol: "AVGO")
    }
}

#Preview("Slow") {
    ZStack {
        AppColors.background.ignoresSafeArea()
        DetailHeaderChartSkeleton(symbol: "AVGO", slowThresholdSeconds: 0.1)
    }
}

#Preview("No symbol") {
    ZStack {
        AppColors.background.ignoresSafeArea()
        DetailHeaderChartSkeleton()
    }
}
