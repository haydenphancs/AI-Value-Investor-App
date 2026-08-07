//
//  MoversToggle.swift
//  ios
//
//  Molecule: the compact, color-coded Gainers | Losers switch inside the
//  "Today's Top Movers" scanner card. A bespoke control because the active
//  segment is tinted per-mode (green for gainers, red for losers) — which the
//  generic `SegmentedTabControl` (single blue selection) doesn't support.
//

import SwiftUI

enum MoversMode: String, CaseIterable {
    case gainers
    case losers
}

struct MoversToggle: View {
    @Binding var mode: MoversMode

    var body: some View {
        HStack(spacing: 3) {
            segment(.gainers, "Gainers", active: AppColors.gainFill)
            segment(.losers, "Losers", active: AppColors.lossFill)
        }
        .padding(3)
        .background(AppColors.surfaceRecessed)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        // NOTE: there is deliberately NO container-level `.fixedSize()` here.
        // It used to be, to stop "Gaine\nrs" — but the per-segment
        // `.lineLimit(1).fixedSize(horizontal:)` below is what actually prevents that,
        // and the container version additionally told the parent HStack "never
        // compress me". That made the sibling card TITLE absorb 100% of any shortfall,
        // which is how "Today's Top Movers" reflowed to four lines at larger type
        // sizes. Removing either guard alone reintroduces one of the two bugs, so
        // `test_ios_a11y_parity.py` pins them as a pair.
    }

    private func segment(_ value: MoversMode, _ label: String, active: Color) -> some View {
        let isActive = mode == value
        return Button {
            withAnimation(.easeInOut(duration: 0.18)) { mode = value }
        } label: {
            Text(label)
                .font(AppTypography.labelSmallEmphasis)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                // The active segment is a SATURATED FILL, so its ink is `textOnAccent`,
                // never the sentiment token. `gain`/`loss` are 4.5:1 TEXT tokens tuned
                // for a card, and on `toggleSelectedBackground` they measured 4.34 / 4.44
                // light and 3.73 dark — this was the app's only sentiment-on-control-
                // surface pairing. `textOnAccent` on gainFill/lossFill is 5.42 / 5.55.
                .foregroundColor(isActive ? AppColors.textOnAccent : AppColors.textMuted)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(isActive ? active : Color.clear)
                .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    StatefulPreviewWrapper(MoversMode.gainers) { MoversToggle(mode: $0) }
        .padding()
        .background(AppColors.cardBackground)
}

/// Small helper so a `@Binding`-driven control can be previewed with live state.
private struct StatefulPreviewWrapper<Value, Content: View>: View {
    @State private var value: Value
    private let content: (Binding<Value>) -> Content
    init(_ initial: Value, @ViewBuilder content: @escaping (Binding<Value>) -> Content) {
        _value = State(initialValue: initial)
        self.content = content
    }
    var body: some View { content($value) }
}
