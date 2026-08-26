//
//  AppTheme.swift
//  ios
//
//  AI Value Investor App - Theme System
//

import SwiftUI

// MARK: - App Colors
//
// APPEARANCE / LIGHT MODE
// -----------------------
// Theme (System / Dark / Light) is driven by `overrideUserInterfaceStyle` on the
// window — see `Core/Utilities/AppearanceManager.swift`. Tokens resolve per
// `userInterfaceStyle` at render time via `Color(lightHex:darkHex:)`, a dynamic
// `UIColor` (see Theme/ColorAdaptive.swift).
//
// EVERY COLOUR TOKEN IS ADAPTIVE. There is no such thing here as a colour that
// "works in both modes" — that premise was wrong and is what made light mode
// unreadable. Measured on `#FFFFFF`, the old constants were: bullish #22C55E
// 2.28:1, neutral #F59E0B 2.15:1, primaryBlue #3B82F6 3.68:1, textMuted #8A93A0
// 3.11:1 — all below the WCAG AA floor of 4.5:1 for body text.
//
// THE THREE RULES
// ---------------
// 1. TEXT tokens clear 4.5:1 against every surface they can land on (card
//    #FFFFFF, page #F4F5F8, nested #EDF0F5 — the nested one binds, costing about
//    0.6 of ratio versus pure white).
// 2. GRAPHIC tokens (`*Graphic`) clear 3.0:1 — the WCAG 1.4.11 bar for a
//    graphical object. They exist so charts stay vivid where vividness is
//    legitimate. Morningstar states the same rule as "never use viz colors for
//    text"; Stripe/Atlassian/Polaris/Google Finance all ship the same split.
// 3. The SHARED token is the TEXT-SAFE one, always. ~154 of the bullish/bearish
//    references are indirect — computed `Color` vars in Models/ and ViewModels/
//    whose value reaches BOTH a `Text` foreground and a chart stroke (see
//    PriceActionViewModel.trendColor). There is no call site to audit, so the
//    default has to be the value that is merely LESS VIVID when it lands in the
//    wrong role, never the one that is UNREADABLE.
//
// ADDING A TOKEN: use `Color(lightHex:darkHex:)`, never `Color(hex:)`. Then add
// it to `auditManifest` below with its role — `ThemeContrastAudit` will assert
// its contrast in both modes at launch in DEBUG, and a regression trips an
// assertion instead of shipping.
struct AppColors {

    // ━━━ SURFACES ━━━
    // Light card↔page separation is only 1.09:1 and that is CORRECT — no design
    // system separates them by luminance (Apple 1.116, Polaris 1.129, Carbon
    // 1.100). In LIGHT a card is distinguished by a BORDER, not by more grey
    // (see `cardEdge`). In DARK there is no border at all, so an outer card is
    // fill-only against the page (the Home screen's look) and a card nested in
    // another card MUST step up to `cardBackgroundNested` or it disappears.
    // See Views/Modifiers/CardSurface.swift.
    static let background = Color(lightHex: "F4F5F8", darkHex: "171B26", boostsUnderIncreasedContrast: false)
    static let cardBackground = Color(lightHex: "FFFFFF", darkHex: "1E2330", boostsUnderIncreasedContrast: false)

    /// Nested / inset surface. ⚠️ LOCKED: this is the darkest surface in light
    /// mode that carries text, so it sets the contrast floor for every text and
    /// semantic token. `caution` (4.50) and `primaryBlue` (4.52) clear it by
    /// ~0.02 — darkening this value breaks them. `ThemeContrastAudit` will say so.
    static let cardBackgroundLight = Color(lightHex: "EDF0F5", darkHex: "252B3B", boostsUnderIncreasedContrast: false)

    /// A card sitting INSIDE another card. Identical to `cardBackground` in light,
    /// one step lighter in dark.
    ///
    /// Exists because dark mode no longer draws a border (`cardEdge`), and a nested
    /// card shares its parent's fill — so without a step up it is 1.00:1 against its
    /// parent and simply ceases to exist. The concrete case: Recent Activities on
    /// Ticker Detail is a `cardFill()` card whose rows are ALSO `cardFill()`, stacked
    /// 8pt apart; up to 73 of them would merge into one unbroken slab.
    ///
    /// NOT `cardBackgroundLight`, whose light arm is #EDF0F5 — using that would visibly
    /// grey these cards in light mode, which must not move. Same dark arm, different
    /// light arm, on purpose.
    static let cardBackgroundNested = Color(lightHex: "FFFFFF", darkHex: "252B3B", boostsUnderIncreasedContrast: false)

    /// Inverted surface for toasts / tooltips — pair with `textInverse`.
    static let surfaceInverse = Color(lightHex: "1E2330", darkHex: "F4F5F8", boostsUnderIncreasedContrast: false)

    /// A constant-LIGHT pill/circle that floats over MEDIA — hero photography,
    /// artwork, a saturated server gradient. Pair with `textOnMediaSurface`.
    ///
    /// Constant in both appearances by design, and it is NOT `surfaceInverse`: that
    /// token flips, because what sits behind a toast IS the app's page. What sits
    /// behind this is a photograph the app did not choose, so following the app's
    /// appearance buys nothing and costs legibility.
    ///
    /// The mirror of `textOnAccent`. This exists because `PlayAudioButton` filled a
    /// white circle and inked it with `AppColors.background` — #F4F5F8 in LIGHT, i.e.
    /// **1.09:1**, an invisible play triangle on every Money Moves hero.
    static let mediaSurface = Color(lightHex: "FFFFFF", darkHex: "FFFFFF", boostsUnderIncreasedContrast: false)

    /// Ink on `mediaSurface` — constant dark, 17.2:1. Never `AppColors.background`.
    static let textOnMediaSurface = Color(lightHex: "171B26", darkHex: "171B26", boostsUnderIncreasedContrast: false)

    /// Scrim behind light ink floating over arbitrary MEDIA (a back button on a hero
    /// image). Constant, and strong enough for the worst case: the backdrop may be a
    /// near-white photo, where `Color.black.opacity(0.35)` composites to #A6A6A6 and
    /// put `textOnAccent` at **2.44:1**. At 0.60 the floor is 5.74:1 on white and
    /// better on anything darker.
    static let mediaScrim = Color(lightHex: "000000", lightAlpha: 0.60, darkHex: "000000", darkAlpha: 0.60)

    // ━━━ TEXT ━━━
    // A three-step ladder: ~17 → ~7.5 → ~5.5. `textSecondary` moved in light even
    // though the old #5C6470 passed (5.98) — once `textMuted` rises to 5.64 the
    // two tiers are no longer distinguishable, and a hierarchy you can't see is
    // not a hierarchy.
    static let textPrimary = Color(lightHex: "111827", darkHex: "FFFFFF")
    static let textSecondary = Color(lightHex: "4B5563", darkHex: "B4BCC8")
    /// Dark value is #9CA3AF, not the #8A93A0 first proposed: the binding dark
    /// surface is `chipUnselectedBackground` (#2D3548, LIGHTER than a card), on
    /// which #8A93A0 measures 3.94:1. Muted text does render on unselected
    /// chips, so the token had to move rather than the manifest.
    static let textMuted = Color(lightHex: "5F6875", darkHex: "9CA3AF")

    /// Disabled / placeholder. Deliberately below 4.5:1 — WCAG 1.4.3 exempts
    /// inactive controls. Never use for content the user must read.
    static let textDisabled = Color(lightHex: "A9B0BB", darkHex: "4C5563", boostsUnderIncreasedContrast: false)

    /// Text/icons sitting ON a saturated fill (`primaryFill`, gain/loss chips,
    /// gradients). Constant white by design in both modes.
    static let textOnAccent = Color(lightHex: "FFFFFF", darkHex: "FFFFFF", boostsUnderIncreasedContrast: false)

    /// Ink on an ADAPTIVE fill — `gainFill` / `lossFill`, which are byte-equal to
    /// `gain` / `loss` and therefore BRIGHT in dark. White on those is 2.28 / 2.77;
    /// this flips to near-black in dark and measures 7.79 / 6.41.
    ///
    /// Byte-identical to `textInverse` today and named separately on purpose, the same
    /// way `cardBackgroundNested` is named separately from `cardBackgroundLight`:
    /// `textInverse` is ink on `surfaceInverse` (a toast), this is ink on a saturated
    /// fill. The intent differs and they may diverge — and keeping them distinct is what
    /// lets `test_text_tokens_never_sit_on_a_fill` still BAN `textInverse` on a fill as
    /// the role confusion it is, while requiring this one.
    ///
    /// `.decorative` in the manifest for exactly the reason `textOnAccent` is: its
    /// contrast IS verified, in REVERSE, by every `carries: .onFill` spec — the only
    /// direction that means anything for an ink token.
    static let textOnFill = Color(lightHex: "FFFFFF", darkHex: "111827", boostsUnderIncreasedContrast: false)

    /// Text on `surfaceInverse`.
    static let textInverse = Color(lightHex: "FFFFFF", darkHex: "111827", boostsUnderIncreasedContrast: false)

    // ━━━ SENTIMENT — text-safe (the shared default, see rule 3) ━━━
    // Light values sit in the 4.5–6.25:1 band where 7 of 9 surveyed investing
    // apps land (Fidelity 6.25, Morningstar 5.97, Koyfin 5.96, Yahoo 5.21,
    // Wealthfront 4.80, Schwab 4.53, Bloomberg 4.51). Robinhood's #00C805 is
    // 2.27:1 and is not a model to copy.
    // gain 5.42 vs loss 5.55 are matched within 0.13 so neither sentiment shouts.
    static let gain = Color(lightHex: "0F7A3D", darkHex: "22C55E")
    static let loss = Color(lightHex: "CC1F1F", darkHex: "F87171")
    static let caution = Color(lightHex: "9A6100", darkHex: "F59E0B")

    /// Legacy aliases. Prefer `gain` / `loss` / `caution`: hue-neutral naming
    /// matters because CN/JP/TW markets render red as UP, and `gainGraphic`
    /// reads as an obvious pair where `bullishGraphic` reads as a second opinion
    /// about the market. Migrated mechanically in a later pass.
    static var bullish: Color { gain }
    static var bearish: Color { loss }
    static var neutral: Color { caution }

    // ━━━ SENTIMENT — graphic role (3:1 bar) ━━━
    // Chart strokes, bars, series fills, sparklines. NOT for text.
    // Enforced by a lint: these must not appear outside the chart layer.
    static let gainGraphic = Color(lightHex: "16A34A", darkHex: "22C55E")
    static let lossGraphic = Color(lightHex: "DC2626", darkHex: "EF4444")
    static let cautionGraphic = Color(lightHex: "C2760B", darkHex: "F59E0B")
    static let accentGraphic = Color(lightHex: "0891B2", darkHex: "06B6D4")
    static let primaryGraphic = Color(lightHex: "3B82F6", darkHex: "3B82F6")

    // ━━━ ACCENTS — text-safe ━━━
    static let primaryBlue = Color(lightHex: "2563EB", darkHex: "60A5FA")
    static let accentCyan = Color(lightHex: "0E7490", darkHex: "06B6D4")
    static let accentYellow = Color(lightHex: "8F6100", darkHex: "FACC15")

    // ━━━ FILLS — saturated backgrounds that carry ink ━━━
    //
    // Buttons, selected chips, badges, destructive actions.
    //
    // ⚠️ TWO FAMILIES. They are NOT interchangeable, and each declares its ink in
    // `auditManifest` via `carries:`.
    //
    // (a) FROZEN + `textOnAccent` (white) — primaryFill, cautionFill, accentCyanFill,
    //     alertPurpleFill, alertOrangeFill. A frozen fill is that colour's LIGHT-mode
    //     text value used in BOTH modes, which falls straight out of the maths: a value
    //     chosen to give 4.5:1 as dark ink on white is, by definition, dark enough to
    //     give 4.5:1 UNDER white ink. One value, both jobs, no second calibration.
    //
    // (b) ADAPTIVE + `textOnFill` (white in light, near-black in dark) — gainFill and
    //     lossFill, which are byte-equal to `gain`/`loss`. Freezing them was correct for
    //     contrast and wrong for the product: the Learn tab ended up with a BRIGHT green
    //     progress bar (a 3:1 graphic, so it may be light) sitting beside a DARK green
    //     "Resume Lessons" button — one hue at two lightnesses, 2.9x apart, in one card.
    //     Darkening the INK instead of the fill fixes the look and improves contrast:
    //     `textOnFill` on gain is 5.42 light / 7.79 dark, on loss 5.55 / 6.41, and the
    //     tile-vs-card step goes 2.89 -> 6.89.
    //
    // WHY ONE INK CANNOT SERVE BOTH: `textOnFill`'s dark arm on the frozen primaryFill
    // #2563EB is 3.35:1, and `textOnAccent` on the adaptive dark arms is 2.28 / 2.77 —
    // WORSE than the defect below that created this whole family. A site inked with the
    // other family's token is a real regression. `test_text_tokens_never_sit_on_a_fill`
    // enforces the split per family.
    //
    // These were silently broken app-wide before, in BOTH modes:
    //   white on primaryBlue #3B82F6  = 3.68:1  (15 buttons)
    //   white on bearish    #EF4444   = 3.35:1  (4 destructive buttons)
    //   white on bullish    #22C55E   = 2.28:1
    static let primaryFill = Color(lightHex: "2563EB", darkHex: "2563EB")
    /// ADAPTIVE — byte-equal to `gain`. Carries `textOnFill`, NOT `textOnAccent`.
    static let gainFill = Color(lightHex: "0F7A3D", darkHex: "22C55E")
    /// ADAPTIVE — byte-equal to `loss`. Carries `textOnFill`, NOT `textOnAccent`.
    static let lossFill = Color(lightHex: "CC1F1F", darkHex: "F87171")
    static let cautionFill = Color(lightHex: "9A6100", darkHex: "9A6100")
    static let accentCyanFill = Color(lightHex: "0E7490", darkHex: "0E7490")
    /// Purple fill for a saturated tile carrying `textOnAccent` ink. The text-role
    /// `alertPurple` lightens to #C084FC in dark, where white on it is 2.64:1 — this
    /// keeps the light value in both modes, giving 6.98:1.
    static let alertPurpleFill = Color(lightHex: "7E22CE", darkHex: "7E22CE")
    /// Orange fill for a saturated badge carrying `textOnAccent` ink. The text-role
    /// `alertOrange` lightens to #F97316 in dark, where white on it is 2.80:1 — this
    /// keeps the light value in both modes, giving 5.18:1.
    static let alertOrangeFill = Color(lightHex: "C2410C", darkHex: "C2410C")

    // ━━━ ALERTS ━━━
    static let alertOrange = Color(lightHex: "C2410C", darkHex: "F97316")
    static let alertPurple = Color(lightHex: "7E22CE", darkHex: "C084FC")
    static var alertBlue: Color { primaryBlue }

    // ━━━ BORDERS / DIVIDERS / ELEVATION ━━━
    // Alpha over the ambient surface, so one token works on page, card and
    // nested alike. This is the piece that was missing entirely — a #FFFFFF card
    // on a #F4F5F8 page is 1.09:1 and reads as a floating blob without an edge.
    static let borderSubtle = Color(lightHex: "101828", lightAlpha: 0.08, darkHex: "FFFFFF", darkAlpha: 0.06)
    static let border = Color(lightHex: "101828", lightAlpha: 0.14, darkHex: "FFFFFF", darkAlpha: 0.10)
    static let borderStrong = Color(lightHex: "101828", lightAlpha: 0.24, darkHex: "FFFFFF", darkAlpha: 0.18)
    static var borderFocus: Color { primaryFill }

    /// The card edge — present in light, ABSENT in dark. Used by `cardSurface` /
    /// `cardFill`, i.e. every resting card and every input in the app.
    ///
    /// Not a fourth step on the `borderSubtle`/`border`/`borderStrong` ramp: it is
    /// `border` with the dark arm zeroed, which is a different AXIS (per-mode
    /// presence, not strength). Hence the role name.
    ///
    /// WHY dark gets none: light separates a card from the page with an edge because
    /// it cannot do it with luminance (#FFFFFF on #F4F5F8 is 1.09:1). Dark has the
    /// same problem (1.10:1) but solves it the way the Home screen already does —
    /// fill alone for an outer card, and a LIGHTER SURFACE (`cardBackgroundNested`)
    /// for a card sitting on another card. A hairline on every card read as noise.
    ///
    /// The dark hex is kept rather than dropped so the value is recoverable: set the
    /// alpha back to 0.10 and the old behaviour returns exactly. 0.05 is the
    /// intermediate Home itself uses on its nested tiles.
    ///
    /// ⚠️ The light arm MUST stay identical to `border` — `ThemeContrastAudit`
    /// asserts that at launch (`auditLightParity`), because the whole premise of this
    /// change is that light mode does not move.
    static let cardEdge = Color(lightHex: "101828", lightAlpha: 0.14, darkHex: "FFFFFF", darkAlpha: 0)

    /// Hairline between rows. Same value as `borderSubtle`; named separately
    /// because the intent differs and they may diverge later.
    ///
    /// ⚠️ `Divider().background(x)` does NOT set a divider's line colour —
    /// `Divider` draws `UIColor.separator` over the top, so 38 of the 49
    /// `Divider()` sites in this app have an inert `.background` tint. Use
    /// `.overlay(AppColors.divider)` (see ProfileView.swift:513) or a plain
    /// `Rectangle().fill(AppColors.divider).frame(height: 1)`.
    static var divider: Color { borderSubtle }

    /// Shadows are HUE-TINTED, not pure black, and light mode uses roughly a
    /// tenth the ink dark mode does. Pure black at dark-mode alpha reads as a
    /// grey smudge on a light page.
    static let shadowKey = Color(lightHex: "101828", lightAlpha: 0.10, darkHex: "000000", darkAlpha: 0.40)
    static let shadowAmbient = Color(lightHex: "101828", lightAlpha: 0.05, darkHex: "000000", darkAlpha: 0.24)

    /// The resting-card shadow — present in light, ABSENT in dark. Backs
    /// `AppShadows.card`, which has exactly ONE consumer (`CardElevation.flat`), so
    /// this cannot leak into the ~20 views that use `shadowAmbient` directly.
    ///
    /// Separate from `shadowAmbient` for that reason alone: a resting card in dark
    /// should match the Home screen, which casts nothing, while chart tooltips and
    /// book covers that reach for `shadowAmbient` by hand keep theirs.
    ///
    /// `.raised` and `.overlay` deliberately keep `shadowKey` — floating chrome and
    /// sheets still need depth in dark; that is now the ONLY thing that lifts them,
    /// since they no longer get a border either.
    ///
    /// ⚠️ Light arm must stay identical to `shadowAmbient` (asserted by
    /// `ThemeContrastAudit.auditLightParity`).
    static let shadowCard = Color(lightHex: "101828", lightAlpha: 0.05, darkHex: "000000", darkAlpha: 0)

    /// Backdrop behind sheets / modals / full-screen covers.
    static let scrim = Color(lightHex: "101828", lightAlpha: 0.32, darkHex: "000000", darkAlpha: 0.60)

    // ━━━ TAB BAR ━━━
    static let tabBarBackground = Color(lightHex: "FFFFFF", darkHex: "171B26", boostsUnderIncreasedContrast: false)
    static var tabBarSelected: Color { primaryBlue }
    static var tabBarUnselected: Color { textMuted }

    // ━━━ CONTROLS ━━━
    static var chipSelectedBackground: Color { primaryFill }
    static let chipUnselectedBackground = Color(lightHex: "E7EAF0", darkHex: "2D3548", boostsUnderIncreasedContrast: false)
    static let toggleBackground = Color(lightHex: "EDF0F5", darkHex: "1E2330", boostsUnderIncreasedContrast: false)
    /// Lightened from #D5DAE3: the old value put `textSecondary` at 4.26:1.
    static let toggleSelectedBackground = Color(lightHex: "E2E6EE", darkHex: "374151", boostsUnderIncreasedContrast: false)

    /// A well that sits BELOW the card it is on — a segmented-control track, an inset CTA.
    /// Measured 1.13:1 (light) / 1.14:1 (dark) against `cardBackground`, and it recedes in
    /// BOTH appearances, which is the whole point of a well.
    ///
    /// NOT `toggleBackground`: that token's dark arm is #1E2330, byte-identical to
    /// `cardBackground`'s, so a track drawn with it is 1.00:1 on a card in dark.
    ///
    /// ⚠️ The LIGHT arm is #EEF1F6, NOT the #E7EAF0 that `MoversToggle` used inline before
    /// this token existed. `primaryBlue` renders on this surface (ScannerCard's expand CTA)
    /// and measures 4.29:1 on #E7EAF0 — below the 4.5 floor, i.e. adopting the inline value
    /// verbatim would have `assertionFailure`d at launch. On #EEF1F6 it is 4.56:1.
    /// `textMuted` on it is 4.98:1 / 7.06:1.
    ///
    /// Known gap this does NOT close: ~15 other views still declare a one-off surface with
    /// an inline `Color(lightHex:darkHex:)`, which is legal (rule 1 bans `Color(hex:)`, not
    /// the adaptive initialiser) and therefore invisible to every guard. `SignalDisclosureRow`
    /// is the next-largest. Promote them when one of them next needs to carry ink.
    static let surfaceRecessed = Color(lightHex: "EEF1F6", darkHex: "14171F", boostsUnderIncreasedContrast: false)

    // ━━━ CHART CHROME ━━━
    /// Gridlines are DECORATIVE and deliberately sit below 3:1 — W3C: "not every
    /// graphical object needs to contrast with its surroundings." Nine charting
    /// libraries converge on rgba(0,0,0,0.06–0.12) for exactly this. Axis LABELS
    /// are text and use `chartAxisLabel` (4.5:1), not this.
    static let chartGridline = Color(lightHex: "101828", lightAlpha: 0.08, darkHex: "FFFFFF", darkAlpha: 0.08)
    static let chartCrosshair = Color(lightHex: "101828", lightAlpha: 0.40, darkHex: "FFFFFF", darkAlpha: 0.40)
    static var chartAxisLabel: Color { textMuted }

    // ━━━ CHART SERIES ━━━
    // Every one of these failed 3:1 on white before; a palette tuned for one
    // background does not survive the other. (IBM Carbon ships two entirely
    // separate 14-colour data-viz palettes and shares only 1 of 14.)
    static let growthBarBlue = Color(lightHex: "3B82F6", darkHex: "5B9CF6")
    static let growthYoYYellow = Color(lightHex: "C2760B", darkHex: "FACC15")
    static let growthSectorGray = Color(lightHex: "7A8494", darkHex: "9CA3AF")

    static let profitGrossMargin = Color(lightHex: "2563EB", darkHex: "3B82F6")
    static let profitOperatingMargin = Color(lightHex: "C2410C", darkHex: "F97316")
    static let profitFCFMargin = Color(lightHex: "7E22CE", darkHex: "A855F7")
    static let profitNetMargin = Color(lightHex: "16A34A", darkHex: "22C55E")
    static let profitSectorAverage = Color(lightHex: "6B7280", darkHex: "9CA3AF")

    static let confidenceDividends = Color(lightHex: "2563EB", darkHex: "3B82F6")
    static let confidenceBuybacks = Color(lightHex: "16A34A", darkHex: "22C55E")
    static let confidenceSharesOutstanding = Color(lightHex: "C2760B", darkHex: "FACC15")

    // ━━━ AI MARK ━━━
    // Was `[.indigo, .cyan]` — SwiftUI SYSTEM colours. In light, `.cyan` resolves
    // to #32ADE6 = 2.54:1 on white, so the right half of every "✨ Insight"
    // wordmark was unreadable. A gradient-filled `Text` has no defined WCAG
    // method, so both stops are required to clear 4.5:1 individually.
    ///
    /// The dark start is #818CF8, not the #6366F1 that mirrors SwiftUI's
    /// `.indigo`: that measured 3.51:1 on a card, so the wordmark's head was
    /// failing in DARK too — the original bug was never light-only.
    static let aiRampStart = Color(lightHex: "4F46E5", darkHex: "818CF8")
    static let aiRampEnd = Color(lightHex: "0E7490", darkHex: "22D3EE")
}

// MARK: - Contrast audit manifest
//
// The machine-readable contract `ThemeContrastAudit` checks at launch in DEBUG.
//
// ⚠️ This proves the PALETTE is sound, not that USAGE is. It asserts each token
// clears its floor on the surfaces declared here; it cannot know that some view
// renders `textMuted` on `toggleSelectedBackground`. Pair it with the greps in
// the light-mode plan.

extension AppColors {

    enum TokenRole {
        /// Body text — WCAG 1.4.3 AA, 4.5:1.
        case text
        /// ≥18pt regular / ≥14pt bold — 3:1. Used sparingly: this app's type
        /// scale tops out at 32pt but most data sits at 10–15pt, so almost
        /// nothing genuinely qualifies. Prefer `.text`.
        case largeText
        /// Graphical object — WCAG 1.4.11, 3:1.
        case graphic
        /// A background. Separation from other surfaces is a border's job.
        case surface
        /// Deliberately exempt (gridlines, disabled state, scrims).
        case decorative
    }

    /// Which ink a FILL is contractually required to carry. An enum rather than the old
    /// `carriesOnAccentText: Bool` because two families now coexist and a Bool cannot say
    /// which: `textOnFill`'s dark arm on the frozen `primaryFill` is 3.35:1 and
    /// `textOnAccent` on the adaptive `gainFill` is 2.28:1, so the wrong one is a real
    /// regression in either direction.
    enum FillInk: String {
        /// White in both modes — the five FROZEN fills, and the server `.fill` role.
        case onAccent
        /// White in light, near-black in dark — the ADAPTIVE `gainFill` / `lossFill`.
        case onFill

        var color: Color { self == .onAccent ? AppColors.textOnAccent : AppColors.textOnFill }
    }

    struct TokenSpec {
        let name: String
        let color: Color
        let role: TokenRole
        /// Keys into `surfaceRegistry` this token is contractually allowed on.
        let surfaces: [String]
        /// Set when a token is a FILL and the check runs the other way round:
        /// assert the ink named here is legible ON it. `nil` = not a fill.
        let carries: FillInk?

        init(_ name: String, _ color: Color, _ role: TokenRole,
             on surfaces: [String] = TokenSpec.contentSurfaces,
             carries: FillInk? = nil) {
            self.name = name
            self.color = color
            self.role = role
            self.surfaces = surfaces
            self.carries = carries
        }

        /// The surfaces content can land on. `cardBackgroundLight` binds.
        ///
        /// `cardBackgroundNested` is listed even though it currently resolves to values that
        /// are already measured (light == cardBackground, dark == cardBackgroundLight): it was
        /// registered in `surfaceRegistry` but named by no spec, so its DARK arm was measured
        /// by nothing. Retuning it — the obvious future edit, since it exists to separate a
        /// nested card — would have dropped `textMuted` to ~3.9:1 across the eight live nested
        /// card sites while the audit still printed ✅.
        /// The CONTROL surfaces are deliberately NOT in here, and that is now a DECISION
        /// rather than a debt. A previous revision of this comment recorded "24 failures"
        /// from adding them and concluded that ten tokens would need retuning. That number
        /// was hypothetical: it was the cross-product of every token against every control
        /// surface, and it was never established that those pairings render. They mostly
        /// do not. The eight live call sites were then enumerated by hand:
        ///
        ///   GrowthPeriodToggle · ProfitPowerPeriodToggle · SignalOfConfidenceViewToggle
        ///     selected   textPrimary on toggleSelectedBackground   14.18 / 10.31
        ///     unselected textMuted   on toggleBackground            4.94 /  6.18
        ///   GrowthMetricChip · GrowthChartSheet · ProfitabilityChartSheet · FundamentalsHistorySheet
        ///     selected   textOnAccent  on chipSelectedBackground(=primaryFill)  5.17
        ///     unselected textSecondary on chipUnselectedBackground   6.27 /  6.40
        ///   TabBarItem
        ///     selected   tabBarSelected(=primaryBlue) on tabBarBackground  5.17 / 6.76
        ///     unselected tabBarUnselected(=textMuted) on tabBarBackground  5.64 / 6.77
        ///   MoversToggle
        ///     selected   textOnAccent on gainFill / lossFill        5.42 / 5.55
        ///     unselected textMuted    on surfaceRecessed             4.98 / 7.06
        ///
        /// Every one passes. `MoversToggle` was the sole exception and the only site in the
        /// app that ever put a SENTIMENT token on a control surface — it inked the selected
        /// segment with `gain`/`loss` over `toggleSelectedBackground` (4.34 light, 4.44
        /// light, 3.73 dark) until it moved to the `*Fill` + `textOnAccent` pair the rules
        /// require. **No sentiment token renders on a control surface any more**, which is
        /// why this list stays at four and the three `on:` overrides below are complete.
        ///
        /// The right way to extend this is a per-token `on:` override naming the surface a
        /// view actually pairs it with — never a widening of this constant, which would
        /// re-measure the same phantom cross-product.
        static let contentSurfaces = [
            "background", "cardBackground", "cardBackgroundLight", "cardBackgroundNested",
        ]
    }

    /// Every STORED colour token, as a struct so `Mirror` can enumerate the
    /// names. `ThemeContrastAudit` cross-checks this against `auditManifest`, so
    /// a token added to `AppColors` but forgotten in the manifest fails at launch
    /// instead of shipping unaudited.
    ///
    /// Add a token above → add it here → add it to `auditManifest`. The audit
    /// tells you if you miss the third step; this struct is what makes that
    /// possible. Computed aliases (`bullish`, `divider`, `tabBarSelected`, …)
    /// are deliberately absent — they forward to a canonical token that is
    /// already covered.
    struct TokenInventory {
        let background = AppColors.background
        let cardBackground = AppColors.cardBackground
        let cardBackgroundLight = AppColors.cardBackgroundLight
        let cardBackgroundNested = AppColors.cardBackgroundNested
        let surfaceInverse = AppColors.surfaceInverse
        let mediaSurface = AppColors.mediaSurface
        let textOnMediaSurface = AppColors.textOnMediaSurface
        let mediaScrim = AppColors.mediaScrim
        let textPrimary = AppColors.textPrimary
        let textSecondary = AppColors.textSecondary
        let textMuted = AppColors.textMuted
        let textDisabled = AppColors.textDisabled
        let textOnAccent = AppColors.textOnAccent
        let textOnFill = AppColors.textOnFill
        let textInverse = AppColors.textInverse
        let gain = AppColors.gain
        let loss = AppColors.loss
        let caution = AppColors.caution
        let gainGraphic = AppColors.gainGraphic
        let lossGraphic = AppColors.lossGraphic
        let cautionGraphic = AppColors.cautionGraphic
        let accentGraphic = AppColors.accentGraphic
        let primaryGraphic = AppColors.primaryGraphic
        let primaryBlue = AppColors.primaryBlue
        let accentCyan = AppColors.accentCyan
        let accentYellow = AppColors.accentYellow
        let primaryFill = AppColors.primaryFill
        let gainFill = AppColors.gainFill
        let lossFill = AppColors.lossFill
        let cautionFill = AppColors.cautionFill
        let accentCyanFill = AppColors.accentCyanFill
        let alertPurpleFill = AppColors.alertPurpleFill
        let alertOrangeFill = AppColors.alertOrangeFill
        let alertOrange = AppColors.alertOrange
        let alertPurple = AppColors.alertPurple
        let borderSubtle = AppColors.borderSubtle
        let border = AppColors.border
        let borderStrong = AppColors.borderStrong
        let cardEdge = AppColors.cardEdge
        let shadowKey = AppColors.shadowKey
        let shadowAmbient = AppColors.shadowAmbient
        let shadowCard = AppColors.shadowCard
        let scrim = AppColors.scrim
        let tabBarBackground = AppColors.tabBarBackground
        let chipUnselectedBackground = AppColors.chipUnselectedBackground
        let toggleBackground = AppColors.toggleBackground
        let toggleSelectedBackground = AppColors.toggleSelectedBackground
        let surfaceRecessed = AppColors.surfaceRecessed
        let chartGridline = AppColors.chartGridline
        let chartCrosshair = AppColors.chartCrosshair
        let growthBarBlue = AppColors.growthBarBlue
        let growthYoYYellow = AppColors.growthYoYYellow
        let growthSectorGray = AppColors.growthSectorGray
        let profitGrossMargin = AppColors.profitGrossMargin
        let profitOperatingMargin = AppColors.profitOperatingMargin
        let profitFCFMargin = AppColors.profitFCFMargin
        let profitNetMargin = AppColors.profitNetMargin
        let profitSectorAverage = AppColors.profitSectorAverage
        let confidenceDividends = AppColors.confidenceDividends
        let confidenceBuybacks = AppColors.confidenceBuybacks
        let confidenceSharesOutstanding = AppColors.confidenceSharesOutstanding
        let aiRampStart = AppColors.aiRampStart
        let aiRampEnd = AppColors.aiRampEnd
    }

    static let tokenInventory = TokenInventory()

    static let surfaceRegistry: [String: Color] = [
        "background": background,
        "cardBackground": cardBackground,
        "cardBackgroundLight": cardBackgroundLight,
        "cardBackgroundNested": cardBackgroundNested,
        "chipUnselectedBackground": chipUnselectedBackground,
        "toggleBackground": toggleBackground,
        "toggleSelectedBackground": toggleSelectedBackground,
        "surfaceRecessed": surfaceRecessed,
        "tabBarBackground": tabBarBackground,
        "surfaceInverse": surfaceInverse,
        "mediaSurface": mediaSurface,
    ]

    static let auditManifest: [TokenSpec] = [
        // Text — every control surface included, because these do land on chips
        // and toggles (that is how toggleSelectedBackground got caught at 4.26).
        TokenSpec("textPrimary", textPrimary, .text,
                  on: TokenSpec.contentSurfaces + ["chipUnselectedBackground", "toggleBackground", "toggleSelectedBackground", "tabBarBackground"]),
        TokenSpec("textSecondary", textSecondary, .text,
                  on: TokenSpec.contentSurfaces + ["chipUnselectedBackground", "toggleBackground", "toggleSelectedBackground"]),
        TokenSpec("textMuted", textMuted, .text,
                  on: TokenSpec.contentSurfaces + ["chipUnselectedBackground", "toggleBackground", "tabBarBackground", "surfaceRecessed"]),
        TokenSpec("textInverse", textInverse, .text, on: ["surfaceInverse"]),
        TokenSpec("textOnMediaSurface", textOnMediaSurface, .text, on: ["mediaSurface"]),
        TokenSpec("textDisabled", textDisabled, .decorative),

        // Sentiment / accents — text-safe role.
        TokenSpec("gain", gain, .text),
        TokenSpec("loss", loss, .text),
        TokenSpec("caution", caution, .text),
        // `tabBarSelected` forwards to this, so it renders on `tabBarBackground` at every
        // launch (TabBarItem.swift) — a live pairing that no spec named until now, i.e. the
        // app's single most-visible accent was unmeasured. 5.17 light / 6.76 dark.
        // `surfaceRecessed` is ScannerCard's expand-CTA track: 4.57 light / 7.05 dark, and
        // it is the reason that token's light arm is #EEF1F6 rather than #E7EAF0 (4.29).
        TokenSpec("primaryBlue", primaryBlue, .text,
                  on: TokenSpec.contentSurfaces + ["tabBarBackground", "surfaceRecessed"]),
        TokenSpec("accentCyan", accentCyan, .text),
        TokenSpec("accentYellow", accentYellow, .text),
        TokenSpec("alertOrange", alertOrange, .text),
        TokenSpec("alertPurple", alertPurple, .text),
        TokenSpec("aiRampStart", aiRampStart, .text),
        TokenSpec("aiRampEnd", aiRampEnd, .text),

        // Fills — checked in REVERSE: is on-accent text legible ON this?
        TokenSpec("primaryFill", primaryFill, .text, on: [], carries: .onAccent),
        TokenSpec("gainFill", gainFill, .text, on: [], carries: .onFill),
        TokenSpec("lossFill", lossFill, .text, on: [], carries: .onFill),
        TokenSpec("cautionFill", cautionFill, .text, on: [], carries: .onAccent),
        TokenSpec("accentCyanFill", accentCyanFill, .text, on: [], carries: .onAccent),
        TokenSpec("alertPurpleFill", alertPurpleFill, .text, on: [], carries: .onAccent),
        TokenSpec("alertOrangeFill", alertOrangeFill, .text, on: [], carries: .onAccent),

        // Graphic role — 3:1.
        TokenSpec("gainGraphic", gainGraphic, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("lossGraphic", lossGraphic, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("cautionGraphic", cautionGraphic, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("accentGraphic", accentGraphic, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("primaryGraphic", primaryGraphic, .graphic, on: ["background", "cardBackground"]),

        // Chart series — 3:1.
        TokenSpec("growthBarBlue", growthBarBlue, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("growthYoYYellow", growthYoYYellow, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("growthSectorGray", growthSectorGray, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("profitGrossMargin", profitGrossMargin, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("profitOperatingMargin", profitOperatingMargin, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("profitFCFMargin", profitFCFMargin, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("profitNetMargin", profitNetMargin, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("profitSectorAverage", profitSectorAverage, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("confidenceDividends", confidenceDividends, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("confidenceBuybacks", confidenceBuybacks, .graphic, on: ["background", "cardBackground"]),
        TokenSpec("confidenceSharesOutstanding", confidenceSharesOutstanding, .graphic, on: ["background", "cardBackground"]),

        // Exempt by design — listed so the exemption is DOCUMENTED, not silent.
        // `ThemeContrastAudit.auditManifestCompleteness()` requires every stored
        // token to appear here, so "not a foreground colour" has to be stated
        // rather than assumed. It caught all 13 entries below on its first run.
        TokenSpec("chartGridline", chartGridline, .decorative),
        TokenSpec("chartCrosshair", chartCrosshair, .decorative),
        TokenSpec("borderSubtle", borderSubtle, .decorative),
        TokenSpec("border", border, .decorative),
        TokenSpec("borderStrong", borderStrong, .decorative),
        // Transparent in dark BY DESIGN — `.decorative` is what lets that through.
        // `auditLightParity()` covers the half that matters: the light arm must stay
        // equal to `border` / `shadowAmbient`.
        TokenSpec("cardEdge", cardEdge, .decorative),

        // Shadows and scrims are alpha over an arbitrary backdrop — there is no
        // fixed pair to measure, and they carry no information.
        TokenSpec("shadowKey", shadowKey, .decorative),
        TokenSpec("shadowAmbient", shadowAmbient, .decorative),
        TokenSpec("shadowCard", shadowCard, .decorative),
        TokenSpec("scrim", scrim, .decorative),
        // Alpha over media the app did not choose, so there is no fixed pair to
        // measure. Its floor is argued at the declaration instead: 0.60 puts
        // `textOnAccent` at 5.74:1 even over a pure-white photo.
        TokenSpec("mediaScrim", mediaScrim, .decorative),

        // Surfaces. Deliberately floor-0: page↔card separation is a BORDER's
        // job, not a luminance job (Apple 1.116:1, Polaris 1.129, Carbon 1.100
        // all sit where ours does). What must pass is the TEXT on them, and
        // every text token above names these in its `surfaces:` list.
        TokenSpec("background", background, .surface),
        TokenSpec("cardBackground", cardBackground, .surface),
        TokenSpec("cardBackgroundLight", cardBackgroundLight, .surface),
        TokenSpec("cardBackgroundNested", cardBackgroundNested, .surface),
        TokenSpec("surfaceInverse", surfaceInverse, .surface),
        TokenSpec("mediaSurface", mediaSurface, .surface),
        TokenSpec("tabBarBackground", tabBarBackground, .surface),
        TokenSpec("chipUnselectedBackground", chipUnselectedBackground, .surface),
        TokenSpec("toggleBackground", toggleBackground, .surface),
        TokenSpec("toggleSelectedBackground", toggleSelectedBackground, .surface),
        TokenSpec("surfaceRecessed", surfaceRecessed, .surface),

        // On-accent ink. Its contrast IS verified — in reverse, by every
        // `carriesOnAccentText` fill above, which is the only direction that
        // means anything for it.
        TokenSpec("textOnAccent", textOnAccent, .decorative),
        // Ink on the two ADAPTIVE fills. `.decorative` for the same reason as
        // `textOnAccent`: verified in REVERSE by every `carries: .onFill` spec.
        TokenSpec("textOnFill", textOnFill, .decorative),
    ]
}

// MARK: - App Typography
//
// 5 Semantic Levels: Title → Heading → Body → Label → Caption
// Plus specialized tiers for financial data (rounded) and SF Symbol icons.
//
// TWO COMFORT TIERS (the rule that keeps cards balanced — modeled on
// Robinhood/Webull/Yahoo Finance): READING PROSE breathes, DENSE DATA stays tight.
//   • Reading prose (insight/narrative/description sentences) → `body` (15, Apple Subheadline — compact, data-dense like Webull).
//   • De-emphasize metadata with COLOR (textMuted/textSecondary), NOT by
//     shrinking below ~13 — never render a human-readable sentence at caption
//     sizes (≤11) or captionTiny (9).
//   • Chart axes, table cells, badges = the DATA tier → caption/labelSmall
//     (10–12) is correct; keep them small.
// Report-card ladder: badge 11 · metadata 13 (label) · prose 15 (body) ·
//   section title 16 (headingSmall) · hero number 16–20 (dataHeading). A section
//   header must never be smaller than the prose beneath it.
//

struct AppTypography {

    // MARK: - Dynamic Type
    //
    // MEASURED, not assumed: `Font.system(size:)` does NOT scale with the user's text
    // size. `TypographyProbe` renders both constructions across the range and prints
    //
    //     Font.system(size: 15)   FIXED  | xSmall=18.0 … AX5=18.0
    //     Font.body (text style)  SCALES | xSmall=18.7 … AX5=69.0
    //
    // so all 40 tokens below — and therefore all ~2,229 call sites — were inert for
    // every user who has ever changed their text size. There is no `relativeTo:`
    // overload for `.system(size:)`, so the scaling has to be computed.
    //
    // WHY AT THE TOKEN, NOT THE CALL SITE
    // -----------------------------------
    // The alternative is a `.appFont(_:)` ViewModifier, which means editing 2,229 sites
    // across 455 files and cannot serve the 51 places that pass a `Font` as a VALUE
    // (a modifier returns `some View`, not a `Font`). Scaling here keeps every call
    // site untouched and covers all three shapes.
    //
    // THE CLAMP, AND WHY THE TWO TIERS DIFFER
    // ---------------------------------------
    // At AX5 an unclamped scale is ~3.5×, which turns a 4-column stat row into one
    // column per screen. The file's own two-tier rule says reading prose breathes while
    // dense data stays tight, and that rule is exactly what the caps encode:
    //
    //   • READING tiers (title/heading/body/label/caption) cap at `readingCap` = 1.4×.
    //     Prose can reflow; this is the tier a low-vision user actually needs, so it
    //     gets the larger of the two caps.
    //   • DATA and ICON tiers cap at `dataCap` = 1.25×. These live in fixed-width
    //     columns, chart axes and `.frame(width:height:)` badges, where growth does not
    //     reflow, it truncates or overlaps.
    //
    // Read the caps off `readingCap`/`dataCap` below, not off this prose — an earlier
    // revision of this comment said 2.0× / 1.4×, which were the values BEFORE the
    // measurement described below forced them down.
    //
    // The caps are a deliberate, stated limitation rather than an oversight — the
    // honest position while the 455-file layout sweep at AX5 is still outstanding.
    // Raising them is a layout project, not a token edit.

    /// Scale `size` the way `style` scales, capped at `maxScale`.
    ///
    /// `UIFontMetrics.scaledValue(for:)` reads the CURRENT content size category, and
    /// these tokens are computed `static var`s, so each render resolves afresh. The root
    /// view reads `\.dynamicTypeSize` (see `iosApp`), which is what makes SwiftUI
    /// re-evaluate the tree when the category changes rather than keeping stale sizes.
    static func scaledSize(_ size: CGFloat,
                                       _ style: UIFont.TextStyle,
                                       maxScale: CGFloat) -> CGFloat {
        let scaled = UIFontMetrics(forTextStyle: style).scaledValue(for: size)
        return min(scaled, size * maxScale)
    }

    /// How far each tier is allowed to grow. MEASURED, not chosen by feel: at 2.0× the
    /// Home "Today's Top Movers" card reflowed to ONE CHARACTER PER LINE and the tab-bar
    /// labels split mid-word ("Researc/h"). Bigger text that cannot be read is not an
    /// accommodation.
    ///
    /// ⚠️ That measurement was taken BEFORE these caps existed and before the
    /// `MoversToggle` / `MarketPulseCard` / `TabBarItem` fixes, so it justifies the caps
    /// rather than describing today's behaviour. Re-measure before quoting it.
    ///
    /// 1.4 / 1.25 is what the CURRENT layouts absorb. Raising it is gated on the
    /// fixed-frame sweep across the view layer (~71 text-bearing fixed frames and ~89
    /// unguarded `lineLimit(1)` sites remain), not on editing these two numbers.
    /// `test_ios_a11y_parity.py` pins both values against this doc block.
    static let readingCap: CGFloat = 1.4
    static let dataCap: CGFloat = 1.25

    /// Reading tier — prose that can reflow.
    fileprivate static func scaled(_ size: CGFloat, _ style: Font.TextStyle,
                                   weight: Font.Weight = .regular,
                                   design: Font.Design = .default) -> Font {
        .system(size: scaledSize(size, style.uiKit, maxScale: readingCap),
                weight: weight, design: design)
    }

    /// Data / icon tier — fixed columns, chart axes, sized badges. Capped at `dataCap`.
    fileprivate static func scaledTight(_ size: CGFloat, _ style: Font.TextStyle,
                                        weight: Font.Weight = .regular,
                                        design: Font.Design = .default) -> Font {
        .system(size: scaledSize(size, style.uiKit, maxScale: dataCap),
                weight: weight, design: design)
    }

    // ━━━ TITLE (Level 1) ━━━
    // Hero content, screen titles, major section headers
    static var titleHero    : Font { scaled(32, .largeTitle, weight: .bold) }
    static var titleLarge   : Font { scaled(28, .largeTitle, weight: .bold) }
    static var title        : Font { scaled(22, .title2, weight: .bold) }
    static var titleCompact : Font { scaled(20, .title3, weight: .semibold) }

    // ━━━ HEADING (Level 2) ━━━
    // Section & card headers, emphasized inline content
    static var heading      : Font { scaled(18, .title3, weight: .semibold) }
    static var headingSmall : Font { scaled(16, .headline, weight: .semibold) }

    // ━━━ BODY (Level 3) ━━━
    // Primary readable content
    static var bodyEmphasis      : Font { scaled(15, .subheadline, weight: .semibold) }
    static var body              : Font { scaled(15, .subheadline, weight: .regular) }
    static var bodySmallEmphasis : Font { scaled(14, .subheadline, weight: .semibold) }
    static var bodySmall         : Font { scaled(14, .subheadline, weight: .regular) }

    // ━━━ LABEL (Level 4) ━━━
    // Metadata, supporting info, subtitles
    static var labelEmphasis      : Font { scaled(13, .footnote, weight: .semibold) }
    static var label              : Font { scaled(13, .footnote, weight: .regular) }
    static var labelSmallEmphasis : Font { scaled(12, .caption, weight: .semibold) }
    static var labelSmall         : Font { scaled(12, .caption, weight: .regular) }

    // ━━━ CAPTION (Level 5) ━━━
    // Micro-copy, badges, chart axes, fine print
    static var captionEmphasis      : Font { scaled(11, .caption2, weight: .semibold) }
    static var caption              : Font { scaled(11, .caption2, weight: .regular) }
    static var captionSmallEmphasis : Font { scaled(10, .caption2, weight: .semibold) }
    static var captionSmall         : Font { scaled(10, .caption2, weight: .regular) }
    static var captionTiny          : Font { scaled(9, .caption2, weight: .regular) }

    // ━━━ DATA (Financial numerics) ━━━
    // Rounded design for numeric emphasis in financial context.
    // .monospacedDigit() = tabular figures so prices/%/columns align and digits
    // don't jitter as values update — the fintech-standard treatment used by
    // Robinhood / Webull / Yahoo Finance. Numerals only (these tokens never back prose).
    static var dataHero    : Font { scaledTight(32, .largeTitle, weight: .bold, design: .rounded).monospacedDigit() }
    static var dataDisplay : Font { scaledTight(28, .largeTitle, weight: .bold, design: .rounded).monospacedDigit() }
    static var dataTitle   : Font { scaledTight(22, .title2, weight: .bold, design: .rounded).monospacedDigit() }
    static var dataHeading : Font { scaledTight(20, .title3, weight: .bold, design: .rounded).monospacedDigit() }
    static var dataLarge   : Font { scaledTight(18, .headline, weight: .bold, design: .rounded).monospacedDigit() }
    static var dataCompact : Font { scaledTight(16, .headline, weight: .bold, design: .rounded).monospacedDigit() }
    static var dataMedium  : Font { scaledTight(14, .subheadline, weight: .bold, design: .rounded).monospacedDigit() }
    static var dataSmall   : Font { scaledTight(10, .caption2, weight: .semibold, design: .rounded).monospacedDigit() }

    // ━━━ ICONS (SF Symbol sizing) ━━━
    // Size-only tokens — add .fontWeight() modifier for weight control
    static var iconSplash  : Font { scaledTight(80, .largeTitle) }
    static var iconHero    : Font { scaledTight(48, .largeTitle) }
    static var iconXXL     : Font { scaledTight(40, .largeTitle) }
    static var iconJumbo   : Font { scaledTight(36, .title) }
    static var iconDisplay : Font { scaledTight(32, .title) }
    static var iconXL      : Font { scaledTight(24, .title3) }
    static var iconLarge   : Font { scaledTight(20, .headline) }
    static var iconMedium  : Font { scaledTight(18, .headline) }
    static var iconDefault : Font { scaledTight(16, .headline) }
    static var iconSmall   : Font { scaledTight(14, .subheadline) }
    static var iconXS      : Font { scaledTight(12, .caption) }
    static var iconTiny    : Font { scaledTight(10, .caption2) }
    static var iconMicro   : Font { scaledTight(8, .caption2) }
}

/// SwiftUI `Font.TextStyle` → the UIKit style `UIFontMetrics` needs.
///
/// The two enums are separate types with no bridge in the SDK, and `UIFontMetrics` is
/// the only API that can scale an arbitrary point size the way a text style scales.
private extension Font.TextStyle {
    var uiKit: UIFont.TextStyle {
        switch self {
        case .largeTitle: return .largeTitle
        case .title:      return .title1
        case .title2:     return .title2
        case .title3:     return .title3
        case .headline:   return .headline
        case .subheadline: return .subheadline
        case .body:       return .body
        case .callout:    return .callout
        case .footnote:   return .footnote
        case .caption:    return .caption1
        case .caption2:   return .caption2
        // Exhaustive today; a future SwiftUI style falls back to `.body`, which scales
        // correctly — merely on the wrong curve — rather than failing to compile a
        // theme file on a new SDK.
        @unknown default: return .body
        }
    }
}

// MARK: - App Spacing
struct AppSpacing {
    static let xxs: CGFloat = 2
    static let xs: CGFloat = 4
    static let sm: CGFloat = 8
    static let md: CGFloat = 12
    static let lg: CGFloat = 16
    static let xl: CGFloat = 20
    static let xxl: CGFloat = 24
    static let xxxl: CGFloat = 32

    /// Bottom reserve on a scrollable detail tab, clearing the floating "Ask Cay AI" bar.
    ///
    /// SCALES, because what it reserves space for is TEXT — the suggestion chips and the
    /// input bar both grow with the content size while a hard number does not.
    ///
    /// This was a literal `120` duplicated in TWELVE places (every detail tab of Ticker, ETF,
    /// Crypto, Commodity and Index). Once the overlay grew past 120pt the last section of
    /// the page sat underneath it and could not be brought into view: the scroll had
    /// already reached its end, so the content was simply unreachable. That reads to a user
    /// as **"I can't scroll down"** rather than as a layout bug, which is exactly how it
    /// was reported.
    ///
    /// Scaled off `body` at `readingCap`, the same tier the chips and the bar use, so the
    /// reserve grows in step with the thing it is clearing (120 → 168 at the cap).
    static var aiBarReserve: CGFloat {
        AppTypography.scaledSize(120, .body, maxScale: AppTypography.readingCap)
    }
}

// MARK: - App Corner Radius
struct AppCornerRadius {
    static let small: CGFloat = 6
    static let medium: CGFloat = 10
    static let large: CGFloat = 14
    static let extraLarge: CGFloat = 18
    static let pill: CGFloat = 20
}

// MARK: - App Gradients

/// Shared multi-stop fills. Defined once so surfaces that are meant to match
/// cannot drift apart when one of them is restyled.
struct AppGradients {
    /// The Cay-AI mark: indigo → teal, top-leading to bottom-trailing.
    ///
    /// Worn by the "Insight"/"Insights" label and by the AI-summary badge beside
    /// it. These two sit inches apart on the Updates card, so they read as one
    /// element — inlining the colours in each would let a later edit to one
    /// silently break the pairing.
    ///
    /// Built from `AppColors.aiRamp*` rather than SwiftUI's `.indigo`/`.cyan`:
    /// the system colours put the tail of the wordmark at 2.54:1 in light mode.
    static let ai = LinearGradient(
        colors: [AppColors.aiRampStart, AppColors.aiRampEnd],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    /// The same ramp at badge-fill strength. Kept beside `ai` so the tint always
    /// tracks the text it sits behind.
    static let aiSubtle = LinearGradient(
        colors: [AppColors.aiRampStart.opacity(0.18), AppColors.aiRampEnd.opacity(0.18)],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )
}

// MARK: - App Shadows
//
// Elevation is NOT symmetric between modes. In light a card is lifted by a
// shadow; in dark it is lifted by a lighter surface, and a shadow at light-mode
// alpha is invisible against #171B26 (black at 4–15% over a dark background
// measures ~1.01:1). Hence `shadowKey`/`shadowAmbient` carry very different
// alphas per mode — and are hue-tinted, not pure black, so they don't read as a
// grey smudge on a light page.
struct AppShadows {
    /// Resting card. Pairs with `AppColors.cardEdge`: in LIGHT the border does most
    /// of the separating and this only softens the edge; in DARK both are absent, so
    /// a resting card is fill-only — the Home screen's look, which is the reference.
    ///
    /// Backed by `shadowCard` (not `shadowAmbient`) purely so zeroing the dark arm
    /// cannot leak into the ~20 views that reach for `shadowAmbient` by hand.
    static let card = Shadow(color: AppColors.shadowCard, radius: 8, x: 0, y: 2)
    /// Lifted: buttons, floating bars, selected states. KEEPS its dark shadow — with
    /// borders gone this is the only thing that lifts floating chrome off the page.
    static let raised = Shadow(color: AppColors.shadowKey, radius: 12, x: 0, y: 4)
    /// Sheets, popovers, anything over a scrim.
    static let overlay = Shadow(color: AppColors.shadowKey, radius: 24, x: 0, y: 12)

    /// Legacy names retained so existing references keep resolving.
    static var cardShadow: Shadow { card }
    static var buttonShadow: Shadow { raised }
}

struct Shadow {
    let color: Color
    let radius: CGFloat
    let x: CGFloat
    let y: CGFloat
}

extension View {
    /// Apply a themed shadow. Prefer this over an inline
    /// `.shadow(color: .black.opacity(x))` — a black shadow tuned for dark mode
    /// reads as dirt on a light page.
    func appShadow(_ shadow: Shadow) -> some View {
        self.shadow(color: shadow.color, radius: shadow.radius, x: shadow.x, y: shadow.y)
    }
}
