//
//  AlertCategoryIcon.swift
//  ios
//
//  Atom: the tinted circular glyph that marks what an alert is ABOUT.
//
//  One circle, used by every alert-shaped row: the "Upcoming & Events" digest cards
//  (`AppAlert`), the notification rows (`NotificationEventDTO`) and the price rules. It
//  used to take an `AppAlert` and was therefore unusable by the other two — so
//  `AlertCardView` had inlined its own byte-for-byte copy and notifications simply had
//  no icon at all, which is most of why the three read as different apps.
//
//  Takes a glyph and a colour, not a model, so it is a real Atom: it knows nothing about
//  the app's domain and every caller maps its own model to those two values.
//

import SwiftUI

struct AlertCategoryIcon: View {
    let systemName: String
    let color: Color
    var size: CGFloat = 40

    /// The glyph is a meaningful icon, so it carries the 4.5:1 TEXT bar — callers pass a
    /// text-role token. The 0.15 wash behind it is decoration and carries nothing.
    private var iconSize: CGFloat { size * 0.45 }

    var body: some View {
        ZStack {
            Circle()
                .fill(color.opacity(0.15))
                .frame(width: size, height: size)

            Image(systemName: systemName)
                .font(.system(size: iconSize, weight: .semibold))
                .foregroundColor(color)
        }
        // The row's accessibility label already names the category in words.
        .accessibilityHidden(true)
    }
}

extension AlertCategoryIcon {
    /// Convenience for the digest cards, which carry their own glyph + tint.
    init(alert: AppAlert, size: CGFloat = 40) {
        self.init(systemName: alert.iconName, color: alert.iconColor, size: size)
    }
}

#Preview {
    VStack(spacing: 20) {
        HStack(spacing: 20) {
            ForEach(AppAlert.sampleData) { alert in
                AlertCategoryIcon(alert: alert)
            }
        }
        HStack(spacing: 20) {
            AlertCategoryIcon(systemName: "person.badge.key.fill", color: AppColors.alertOrange)
            AlertCategoryIcon(systemName: "sparkles", color: AppColors.primaryBlue)
            AlertCategoryIcon(systemName: "bell.badge", color: AppColors.caution)
        }
    }
    .padding()
    .background(AppColors.background)
}
