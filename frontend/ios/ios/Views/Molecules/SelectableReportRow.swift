//
//  SelectableReportRow.swift
//  ios
//
//  Molecule: Wraps a ReportCard with multi-select chrome. In selection mode
//  the card slides right to reveal a leading SelectionCircle and the whole
//  row toggles selection instead of navigating.
//
//  ReportCard is intentionally left UNCHANGED. It is `.disabled` while a
//  report is `.processing`, which would swallow a tap gesture layered inside
//  it — so a transparent tap-catching overlay (present only while selecting)
//  owns the toggle and reliably beats the inner Button for every status.
//

import SwiftUI

struct SelectableReportRow: View {
    let report: AnalysisReport
    let isSelecting: Bool
    let isSelected: Bool
    var onTap: (() -> Void)?
    var onRetry: (() -> Void)?
    var onToggleSelect: (() -> Void)?

    /// Width of the lane revealed for the selection circle. The card shifts
    /// right by exactly this much when entering selection mode.
    private let circleLane: CGFloat = 36

    var body: some View {
        HStack(spacing: 0) {
            if isSelecting {
                SelectionCircle(isSelected: isSelected)
                    .frame(width: circleLane, alignment: .leading)
                    .transition(.move(edge: .leading).combined(with: .opacity))
            }

            ReportCard(
                report: report,
                onTap: isSelecting ? nil : onTap,
                onRetry: isSelecting ? nil : onRetry
            )
        }
        // While selecting, a transparent catcher over the WHOLE row (circle +
        // card) toggles selection — so tapping the circle works too, not just
        // the card. Absent in normal mode, so navigation + Retry behave as usual.
        .overlay {
            if isSelecting {
                Color.clear
                    .contentShape(Rectangle())
                    .onTapGesture { onToggleSelect?() }
            }
        }
        .animation(.easeInOut(duration: 0.22), value: isSelecting)
        .accessibilityAddTraits(isSelecting && isSelected ? .isSelected : [])
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        SelectableReportRow(report: AnalysisReport.mockReports[0],
                            isSelecting: false, isSelected: false)
        SelectableReportRow(report: AnalysisReport.mockReports[0],
                            isSelecting: true, isSelected: false)
        SelectableReportRow(report: AnalysisReport.mockReports[0],
                            isSelecting: true, isSelected: true)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
