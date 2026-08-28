//
//  ShareContent.swift
//  ios
//
//  Utility: builds the activity items for every share in the app.
//
//  WHY THIS EXISTS: a TestFlight tester sharing ORCL asked for "the link to download the
//  app". The share sheet was producing a single plain String and nothing else —
//
//      Oracle Corporation (ORCL)
//      $118.42 +1.24 +1.06%
//
//      Check it out on Caydex!
//
//  — so the recipient had a price and a brand name and no way to act on either. The
//  feature had in fact been scaffolded and abandoned: TickerDetailView carried a
//  commented-out `items.append(URL(string: "https://yourapp.com/stock/\(ticker)")!)`.
//
//  The payload was also built INLINE at eleven call sites, five of them byte-identical
//  copies of the same twenty lines, with no test anywhere. One helper is what stops a
//  sixth copy from drifting.
//

import Foundation

enum ShareContent {

    /// The one line every share ends with. Attribution rather than a pitch: the link below
    /// it does the inviting, and this reads the same whether the thing shared is a stock, a
    /// news article, a book or a research report.
    static let attribution = "Shared from Caydex"

    /// Activity items for a share: the caller's text, our attribution, and a link the
    /// recipient can use to get the app.
    ///
    /// - Parameters:
    ///   - body: what is being shared, already formatted. May be empty — a share tapped
    ///     while a screen is still loading degrades to attribution + link rather than to
    ///     `UIActivityViewController` with ZERO items, which is what the five asset-detail
    ///     screens used to present because they built the payload inside `if let data`.
    ///   - extras: items that must lead the array. `UIActivityViewController` picks the
    ///     activity from the item TYPES, so a file (the report PDF) or a publisher URL has
    ///     to come FIRST or "Save to Files" / AirDrop see a text share instead.
    ///
    /// The download link is a SEPARATE activity item, deliberately not interpolated into
    /// the string: Messages and Mail render a separate URL item as a link preview, while a
    /// URL buried in text is just text.
    static func items(_ body: String, attaching extras: [Any] = []) -> [Any] {
        let trimmed = body.trimmingCharacters(in: .whitespacesAndNewlines)
        let caption = trimmed.isEmpty ? attribution : "\(trimmed)\n\n\(attribution)"
        return extras + [caption, AppInfo.downloadURL]
    }
}
