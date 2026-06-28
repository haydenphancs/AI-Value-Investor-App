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
            segment(.gainers, "Gainers", active: AppColors.bullish)
            segment(.losers, "Losers", active: AppColors.bearish)
        }
        .padding(3)
        .background(Color(hex: "14171F"))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        // Keep the toggle at its intrinsic width so a long card title can't
        // squeeze the labels onto a second line ("Gaine\nrs").
        .fixedSize()
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
                .foregroundColor(isActive ? active : AppColors.textMuted)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(isActive ? Color(hex: "2B3344") : Color.clear)
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
