//
//  HitSlop.swift
//  ios
//
//  Grow a control's TOUCH area without growing the control.
//
//  WHY THIS EXISTS
//  ---------------
//  Reported from TestFlight: "The Back icon is hard to click on it sometimes
//  which could be a problem for many users / elderly people. Is this called
//  hitslop in software?"
//
//  Yes — "hit slop" is the React Native name for it; UIKit does the same thing by
//  overriding `point(inside:with:)`, and Android calls it a TouchDelegate. SwiftUI
//  has no built-in spelling, so this is it.
//
//  The header chrome on all five detail screens draws its icons in a 40x40pt box.
//  Apple's minimum comfortable target is 44x44 (HIG, Accessibility → Buttons and
//  Controls), and these sit in the top-left corner where thumb reach is worst, so
//  40 is a target people miss — exactly as reported.
//
//  THE IDIOM, AND WHY IT IS THREE MODIFIERS
//  ----------------------------------------
//      .padding(inset)            // grow the view
//      .contentShape(Rectangle()) // make the GROWN box hit-testable
//      .padding(-inset)           // give the LAYOUT its original size back
//
//  The negative padding is the whole trick: touch area grows, layout does not
//  move, and the glyph keeps its own font size. Enlarging the frame instead would
//  push every neighbour along and make the icons look heavier, which is precisely
//  what the reporter did not ask for.
//
//  `contentShape` is not optional here. Without it the hit area follows whatever
//  SwiftUI decides the content's shape is, which is an implementation detail that
//  has differed across releases — and this bug was reported on iOS 18.7.8 while
//  the simulator that reproduces it runs iOS 26. Declaring the shape makes the
//  target the same on both.
//
//  ⚠️ SLOP IS CLIPPED BY THE PARENT'S BOUNDS. IT IS NOT A SUBSTITUTE FOR SIZE.
//  Measured on the index detail header (iPhone 17 Pro): the row is an `HStack`
//  with no vertical padding, so its height is exactly its tallest child. With
//  40pt icons the row was 40pt tall, and slop bought nothing vertically — a tap
//  6pt below the icon still missed, because the parent rejects the point before
//  the icon is ever asked. Horizontally, where the row spans the full width,
//  the same slop worked: a tap 4pt LEFT of the 40pt box hit.
//
//  So size the control to `minimumTarget` first and treat slop as the margin on
//  top, in whichever directions the parent actually has room. Slop alone, on a
//  control its parent fits tightly, is a no-op that reads like a fix.
//
//  ⚠️ CHOOSING `inset`: NEVER MORE THAN HALF THE GAP TO THE NEXT CONTROL.
//  Overlapping touch areas do not error — SwiftUI silently gives the overlap to
//  whichever sibling is on top — so a too-generous slop turns "hard to hit" into
//  "hit the wrong one", which is worse and much harder to report. At exactly half
//  the gap the areas meet without overlapping and every point goes to the nearer
//  control, which is the most target a row of icons can be given.
//

import SwiftUI

enum HitSlop {
    /// Half of `AppSpacing.md` (12pt), the gap this app puts between header icons.
    /// Applied to the standard 40pt icon box it yields a 52pt target that exactly
    /// tiles with its neighbours — no overlap, no gap, nothing moved.
    static let standard: CGFloat = 6

    /// Apple's minimum comfortable target, for reference in tests and reviews.
    static let minimumTarget: CGFloat = 44
}

extension View {
    /// Extends the touch area `inset` points beyond this view on every side,
    /// leaving layout and the rendered content untouched.
    ///
    /// Keep `inset` at or below half the distance to the neighbouring control —
    /// see the file header for why overlapping is worse than under-shooting.
    func hitSlop(_ inset: CGFloat = HitSlop.standard) -> some View {
        self
            .padding(inset)
            .contentShape(Rectangle())
            .padding(-inset)
    }

    /// Slop sized to bring a `box`-point control up to `HitSlop.minimumTarget`,
    /// never exceeding `maxInset`.
    ///
    /// Prefer this over a bare number at the call site: it says WHY the number is
    /// what it is, and it keeps the 44pt intent in one place instead of scattering
    /// two dozen hand-tuned constants across the view layer.
    ///
    /// `maxInset` is the cap that keeps neighbouring controls from overlapping —
    /// pass half the gap to the nearest one. It is a CAP, not a target: a 36pt
    /// control needs only 4pt to reach 44 and will take 4, not the default 6.
    func hitSlop(reaching box: CGFloat, maxInset: CGFloat = HitSlop.standard) -> some View {
        hitSlop(min(maxInset, max(0, (HitSlop.minimumTarget - box) / 2)))
    }
}
