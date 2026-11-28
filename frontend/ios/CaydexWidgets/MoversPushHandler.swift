//
//  MoversPushHandler.swift
//  CaydexWidgets
//
//  WidgetKit push (iOS 26+): the server may ask WidgetKit to rebuild the Movers timeline after
//  the Insights sweeper stores a card for a device's head ticker, or a market card (owner
//  2026-11-27: "Yes, add push too"). The push carries NO data — `{"aps":{"content-changed":true}}`
//  — so a wake runs exactly the timeline a scheduled reload runs, with the same fences.
//
//  ⚠️ THIS FILE ONLY LOGS. The push token is uploaded by the APP (`PUT /widget/push-token`, a
//  session route): this process holds no session (`auth.md` §8, §8a), so it makes no request,
//  writes nothing to the App Group, reads no credential and never asks for a reload. WidgetKit
//  also tells the app the token through `WidgetCenter.currentPushInfo`, which is what the app
//  sends. `test_ios_widget_extension_guards.py` pins that this file stays that small.
//
//  ⚠️ iOS 18.0 IS STILL THE DEPLOYMENT TARGET. Every iOS 26 symbol is behind `@available` or
//  `#available`; below 26 the configuration is returned unchanged and the tile keeps its
//  timeline schedule alone.
//

import OSLog
import SwiftUI
import WidgetKit

/// Told by WidgetKit when the widget's push token changes. Nothing to do here but say so.
///
/// `nonisolated`: the target defaults to the main actor, and WidgetKit may call the handler off
/// the main thread (as `MoversConfigurationIntent`'s `perform()` is nonisolated).
@available(iOS 26.0, *)
nonisolated struct MoversPushHandler: WidgetPushHandler {
    private static let log = Logger(subsystem: "com.phan.caydex", category: "widget")

    init() {}

    func pushTokenDidChange(_ pushInfo: WidgetPushInfo, widgets: [WidgetInfo]) {
        // The count only — never the token, which identifies the device to APNs.
        Self.log.info("widget push: token changed for \(widgets.count, privacy: .public) widget(s) — the app uploads it")
    }
}

extension WidgetConfiguration {
    /// Attaches `MoversPushHandler` on iOS 26+, and is the identity below it.
    ///
    /// An `if #available` with two `return`s in a function returning `some WidgetConfiguration`
    /// (SE-0360, opaque result types with limited availability): `Widget.body` has no result
    /// builder that could hold the branch, and `pushHandler(_:)` exists only on iOS 26.
    func caydexPushHandler() -> some WidgetConfiguration {
        if #available(iOS 26.0, *) {
            return pushHandler(MoversPushHandler.self)
        } else {
            return self
        }
    }
}
