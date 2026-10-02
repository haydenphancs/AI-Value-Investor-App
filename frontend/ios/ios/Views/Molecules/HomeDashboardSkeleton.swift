//
//  HomeDashboardSkeleton.swift
//  ios
//
//  Molecule: placeholder Home dashboard shown IN the scroll content during the first load.
//
//  WHY THIS EXISTS. Home's first load used to sit under a full-screen `LoadingOverlay` that
//  dimmed the screen and swallowed every touch — the header's search and profile, and the tab
//  bar — for as long as `GET /home/dashboard` took (TestFlight 1.0 (9): "At initial, it loads so
//  slow"). This draws in the content instead, so everything around it stays live, and it is
//  shaped like the real screen (status line, a strip of pulse tiles, then section cards) so the
//  dashboard replaces it without the page jumping.
//
//  Inert by construction: no Button, no gesture, one VoiceOver label for the whole block. It
//  knows no domain model. Same idioms as `TrackedAssetsSkeleton` and `DetailTabSkeleton`:
//  `ShimmerEffect`'s `.shimmer()`, `cardBackgroundLight` bars on card surfaces.
//

import SwiftUI

struct HomeDashboardSkeleton: View {

    /// `MarketPulseCard`'s width FLOOR (its `.frame(minWidth: 88, …)`). A literal on purpose:
    /// that card's own literal is pinned by `test_ios_market_pulse_equal_width.py`, and the real
    /// tiles grow past it, so this only has to be close enough not to jump.
    private static let pulseTileWidth: CGFloat = 88
    private static let pulseTileCount = 4

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xl) {
            pulseStrip
            sectionPlaceholder(rows: 3)
            sectionPlaceholder(rows: 2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .shimmer()
        // One announcement for the whole block. `children: .ignore` matters: without it the
        // label sits on a container whose bars stay individually reachable.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading your dashboard")
    }

    // MARK: - Market Pulse

    private var pulseStrip: some View {
        VStack(alignment: .leading, spacing: 10) {
            bar(width: 120, height: 14)
                .padding(.horizontal, AppSpacing.lg)

            // Not scrollable: it is a picture of the strip, and the overflow is clipped.
            HStack(spacing: 10) {
                ForEach(0..<Self.pulseTileCount, id: \.self) { _ in
                    pulseTile
                }
            }
            .padding(.horizontal, AppSpacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
            .clipped()
        }
    }

    private var pulseTile: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            bar(width: 52, height: 10)
            bar(width: 60, height: 14)
            bar(width: nil, height: 22)
            bar(width: 40, height: 10)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 9)
        .frame(width: Self.pulseTileWidth, alignment: .topLeading)
        .cardSurface(cornerRadius: 12)
    }

    // MARK: - Sections

    private func sectionPlaceholder(rows: Int) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            bar(width: 150, height: 16)

            VStack(alignment: .leading, spacing: AppSpacing.lg) {
                ForEach(0..<max(1, rows), id: \.self) { _ in
                    row
                }
            }
            .padding(AppSpacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
            .cardSurface()
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    private var row: some View {
        HStack(spacing: AppSpacing.md) {
            bar(width: 32, height: 32)
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                bar(width: 72, height: 12)
                bar(width: nil, height: 10)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            bar(width: 56, height: 14)
        }
    }

    // MARK: - Bar

    /// `width == nil` fills the space offered.
    private func bar(width: CGFloat?, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 4, style: .continuous)
            .fill(AppColors.cardBackgroundLight)
            .frame(width: width, height: height)
            .frame(maxWidth: width == nil ? .infinity : nil, alignment: .leading)
    }
}

#Preview("Light") {
    ScrollView {
        HomeDashboardSkeleton()
            .padding(.top, AppSpacing.sm)
    }
    .background(AppColors.background)
    .environment(\.colorScheme, .light)
}

#Preview("Dark") {
    ScrollView {
        HomeDashboardSkeleton()
            .padding(.top, AppSpacing.sm)
    }
    .background(AppColors.background)
    .environment(\.colorScheme, .dark)
}
