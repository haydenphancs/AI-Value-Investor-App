//
//  StaleDataBanner.swift
//  ios
//
//  Atom: an age warning for point-in-time data.
//
//  Why this exists: a research report is a FROZEN snapshot — its score, card verdicts
//  and analyst target are computed once and never re-fetched. Opened a year later it
//  rendered identically to day zero, with only a small "Previous Close · Mar 4, 2026"
//  line to signal age. Presenting stale financial figures as current is the kind of
//  thing a reader reasonably relies on, so the age needs to be stated, not inferred.
//
//  Generic (Int + enum in, no domain types) so it qualifies as an Atom.
//

import SwiftUI

struct StaleDataBanner: View {
    enum Severity {
        /// Noticeably old — worth flagging, still broadly usable.
        case caution
        /// Old enough that the figures should not be relied on.
        case strong

        var tint: Color {
            switch self {
            case .caution: return AppColors.neutral
            case .strong:  return AppColors.bearish
            }
        }

        var icon: String {
            switch self {
            case .caution: return "clock.badge.exclamationmark"
            case .strong:  return "exclamationmark.triangle.fill"
            }
        }
    }

    let daysOld: Int
    let severity: Severity
    /// What the data describes, used in the message (e.g. "This report").
    let subject: String

    init(daysOld: Int, severity: Severity, subject: String = "This report") {
        self.daysOld = daysOld
        self.severity = severity
        self.subject = subject
    }

    private var message: String {
        let age = Self.agePhrase(daysOld)
        switch severity {
        case .caution:
            return "\(subject) is \(age) old. Figures reflect that date, not today."
        case .strong:
            return "\(subject) is \(age) old. Prices, estimates and ratings have likely "
                + "changed — regenerate it before relying on these figures."
        }
    }

    /// "12 days" / "3 weeks" / "5 months" / "over a year".
    static func agePhrase(_ days: Int) -> String {
        switch days {
        case ..<14:  return "\(max(days, 0)) days"
        case ..<60:  return "\(days / 7) weeks"
        case ..<365: return "\(days / 30) months"
        default:     return "over a year"
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Image(systemName: severity.icon)
                .font(AppTypography.iconXS)
                .foregroundColor(severity.tint)

            Text(message)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(severity.tint.opacity(0.12))
        )
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .stroke(severity.tint.opacity(0.35), lineWidth: 1)
        )
        .accessibilityLabel(message)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            StaleDataBanner(daysOld: 9, severity: .caution)
            StaleDataBanner(daysOld: 21, severity: .caution)
            StaleDataBanner(daysOld: 95, severity: .strong)
            StaleDataBanner(daysOld: 500, severity: .strong)
        }
        .padding()
    }
}
