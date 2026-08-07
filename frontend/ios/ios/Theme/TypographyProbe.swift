//
//  TypographyProbe.swift
//  ios
//
//  DEBUG-only Dynamic Type MEASUREMENT — the fourth theming guard.
//
//  WHY THIS EXISTS
//  ---------------
//  This probe was written to settle a question asserted in both directions: does
//  `Font.system(size:)` scale with the user's text-size setting? It MEASURED the
//  answer — it does NOT (18.0pt flat from xSmall to AX5, while `Font.body` goes
//  18.7 → 69.0) — so all 40 `AppTypography` tokens, and therefore ~2,229 call sites,
//  were inert for every user who had ever changed their text size.
//
//  That finding is now FIXED: the tokens are computed `static var`s that scale via
//  `UIFontMetrics` under the `readingCap`/`dataCap` clamps, and `iosApp` reads
//  `\.dynamicTypeSize` so the tree re-evaluates. The probe stays because it is the
//  only thing that can prove the mechanism still works, and because of the harness
//  trap documented below — which is why the answer must be re-measured, never
//  remembered. Same discipline as `AppearanceProbe`: read the log, not a screenshot.
//
//  Driven across every content-size category by `frontend/ios/scripts/type-sweep.sh`.
//
//  `print` over `os.Logger` for the same reason `AppearanceProbe` gives: the
//  verification recipe captures stdout via `simctl launch --console-pty`, which
//  cannot see the unified log. The file-wide `#if DEBUG` satisfies CLAUDE.md's
//  "never print() in production code".
//

#if DEBUG

import SwiftUI
import UIKit

@MainActor
enum TypographyProbe {

    /// Render one `Text` at a given `DynamicTypeSize` and report the height it takes.
    ///
    /// Measured through a real `UIHostingController` rather than by inspecting the
    /// `Font` value: a `Font` is an opaque description, and the only thing that
    /// answers "did this scale" is what the layout system actually produced.
    private static func measuredHeight(_ font: Font, at size: DynamicTypeSize) -> CGFloat {
        let view = Text("Wg")
            .font(font)
            .fixedSize()
            .dynamicTypeSize(size)
        let host = UIHostingController(rootView: view)
        host.view.backgroundColor = .clear
        let unbounded = CGSize(width: CGFloat.greatestFiniteMagnitude,
                               height: CGFloat.greatestFiniteMagnitude)
        return host.sizeThatFits(in: unbounded).height
    }

    /// Print the scaling behaviour of both font constructions across the type-size range.
    ///
    /// Two rows, and the comparison between them IS the finding:
    ///   • `Font.system(size: 15)`  — the construction `AppTypography` uses 40 times.
    ///   • `Font.body`              — a text style, which is documented to scale.
    /// If row 1 is flat while row 2 grows, Dynamic Type is inert for this app's type
    /// scale and the migration is real work. If both grow, it already works and the
    /// remaining exposure is layout, not typography.
    static func dumpDynamicTypeScaling() {
        let sizes: [(String, DynamicTypeSize)] = [
            ("xSmall", .xSmall), ("large(default)", .large), ("xxxLarge", .xxxLarge),
            ("AX1", .accessibility1), ("AX3", .accessibility3), ("AX5", .accessibility5),
        ]

        func row(_ label: String, _ font: Font) {
            let heights = sizes.map { measuredHeight(font, at: $0.1) }
            let cells = zip(sizes, heights)
                .map { "\($0.0.0)=\(String(format: "%.1f", $0.1))" }
                .joined(separator: "  ")
            let first = heights.first ?? 0
            let last = heights.last ?? 0
            let grew = last > first + 0.5
            print("[TypeProbe] \(label.padding(toLength: 24, withPad: " ", startingAt: 0)) "
                  + "\(grew ? "SCALES" : "FIXED ") | \(cells)")
        }

        // The measurement that matters for the SHIPPING mechanism.
        //
        // `AppTypography` scales via `UIFontMetrics`, which reads the SYSTEM content size
        // category — it cannot see SwiftUI's per-view `.dynamicTypeSize()` override, so
        // the rendered-height rows below will report AppTypography as FIXED even when it
        // is working. That is a limitation of the harness, not of the tokens. Drive the
        // real thing instead:
        //
        //     xcrun simctl ui <udid> content_size accessibility-extra-extra-extra-large
        //
        // then relaunch and compare this line across runs.
        let category = UITraitCollection.current.preferredContentSizeCategory
        let samples = [(15, UIFont.TextStyle.subheadline), (9, .caption2),
                       (32, .largeTitle), (16, .headline)]
        let resolved = samples.map { size, style in
            let raw = UIFontMetrics(forTextStyle: style).scaledValue(for: CGFloat(size))
            return "\(size)→\(String(format: "%.1f", raw))"
        }.joined(separator: "  ")
        print("[TypeProbe] SYSTEM category=\(category.rawValue)")
        print("[TypeProbe] UIFontMetrics unclamped: \(resolved)")
        print("[TypeProbe] AppTypography resolved:  "
              + "body=\(String(format: "%.1f", AppTypography.scaledSize(15, .subheadline, maxScale: AppTypography.readingCap)))  "
              + "captionTiny=\(String(format: "%.1f", AppTypography.scaledSize(9, .caption2, maxScale: AppTypography.readingCap)))  "
              + "dataSmall=\(String(format: "%.1f", AppTypography.scaledSize(10, .caption2, maxScale: AppTypography.dataCap)))  "
              + "iconDefault=\(String(format: "%.1f", AppTypography.scaledSize(16, .headline, maxScale: AppTypography.dataCap)))")

        print("[TypeProbe] ── SwiftUI .dynamicTypeSize() override (rendered heights, pt) ──")
        row("Font.system(size: 15)", .system(size: 15))
        row("AppTypography.body", AppTypography.body)
        row("AppTypography.captionTiny", AppTypography.captionTiny)
        row("Font.body (text style)", .body)
        row("Font.custom relativeTo", .custom("Helvetica", size: 15, relativeTo: .body))

        // The other half of the accessibility axis, printed alongside because it is the
        // same "is this trait wired to anything" question and there is no other place
        // that reports it. Both are currently unread by any code in the app.
        let traits = UITraitCollection.current
        print("[TypeProbe] accessibilityContrast=\(traits.accessibilityContrast.rawValue) "
              + "legibilityWeight=\(traits.legibilityWeight.rawValue) "
              + "preferredContentSizeCategory=\(traits.preferredContentSizeCategory.rawValue) "
              + "reduceTransparency=\(UIAccessibility.isReduceTransparencyEnabled) "
              + "invertColors=\(UIAccessibility.isInvertColorsEnabled) "
              + "differentiateWithoutColor=\(UIAccessibility.shouldDifferentiateWithoutColor)")
    }
}

#endif
