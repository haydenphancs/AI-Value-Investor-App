//
//  ModalPresentationProbe.swift
//  ios
//
//  Whether anything is presented modally right now, read from UIKit — and a bounded wait for
//  the presentation stack to finish coming down.
//
//  WHY: a TAPPED PUSH opens its detail as a sheet from `ContentView`, and a sheet cannot present
//  while another view's sheet or cover is already up. SwiftUI does not drop it — it logs
//  "Currently, only presenting a single sheet is supported. The next sheet will be presented when
//  the currently presented sheet gets dismissed." and QUEUES it. Measured on the simulator: a push
//  tapped while a ticker was open from a Home tile did nothing visible until the user closed the
//  ticker by hand, at which point the notification's detail appeared out of nowhere.
//
//  The fix is `AppState.dismissAllPresentations()` (every tab root clears its own presentation
//  state), then present once UIKit reports the stack is clear. `presentedViewController` stays
//  non-nil until a dismissal's animation COMPLETES, which is exactly the moment a new
//  presentation becomes possible. The view state alone cannot say that — a binding goes nil the
//  instant the dismissal is requested.
//

import UIKit
import os

@MainActor
enum ModalPresentationProbe {

    /// True while any window's root controller is presenting something (a sheet, a cover, a
    /// system share sheet …), including one still animating away.
    static var isAnythingPresented: Bool {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .contains { $0.rootViewController?.presentedViewController != nil }
    }

    /// Wait until nothing is presented, or `timeout` passes. Returns whether it cleared.
    ///
    /// Bounded because a presentation no tab root owns — one of `iosApp`'s own root sheets, or a
    /// system sheet — never comes down on `dismissAllPresentations()`. The caller then presents
    /// anyway and SwiftUI queues it behind that sheet, which is the pre-existing behaviour, now
    /// logged instead of silent.
    static func waitUntilNothingPresented(timeout: Duration = .milliseconds(1500)) async -> Bool {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: timeout)
        while isAnythingPresented {
            if clock.now >= deadline {
                log.warning("presentation stack did not clear within \(String(describing: timeout), privacy: .public) — a presentation no tab root owns is still up; the next sheet will queue behind it")
                return false
            }
            try? await Task.sleep(for: .milliseconds(50))
        }
        return true
    }

    private static let log = Logger(subsystem: "com.phan.caydex", category: "presentation")
}
