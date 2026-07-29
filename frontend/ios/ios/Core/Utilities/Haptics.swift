//
//  Haptics.swift
//  ios
//
//  Central haptic helper gated by the "Haptic Feedback" setting. Call these instead
//  of constructing UI*FeedbackGenerator directly so the toggle actually works.
//

import UIKit

@MainActor
enum Haptics {
    /// Defaults to true when the key was never written (matches the settings default).
    static var isEnabled: Bool {
        UserDefaults.standard.object(forKey: "haptic_feedback") as? Bool ?? true
    }

    static func impact(_ style: UIImpactFeedbackGenerator.FeedbackStyle = .medium) {
        guard isEnabled else { return }
        UIImpactFeedbackGenerator(style: style).impactOccurred()
    }

    static func success() {
        guard isEnabled else { return }
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }

    static func warning() {
        guard isEnabled else { return }
        UINotificationFeedbackGenerator().notificationOccurred(.warning)
    }

    static func selection() {
        guard isEnabled else { return }
        UISelectionFeedbackGenerator().selectionChanged()
    }
}
