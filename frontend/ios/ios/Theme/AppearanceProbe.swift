//
//  AppearanceProbe.swift
//  ios
//
//  DEBUG-only appearance RESOLUTION trace — the third leg of the theming guards.
//
//  WHY THIS EXISTS
//  ---------------
//  `ThemeContrastAudit` proves the PALETTE (every token clears its WCAG floor in
//  both interface styles). `PalettePreview` proves the SWATCHES. Neither can see
//  RESOLUTION: which style the window actually ended up in, and therefore which
//  half of each token was drawn.
//
//  That is the gap "System renders dark on a light OS" fell through, and it is a
//  gap no screenshot can close. A screenshot conflates four separable questions —
//  what was stored, what the window override was set to, what trait the window
//  actually resolved to, and what the token resolved to there — and it silently
//  adds a fifth (did the harness even flip the OS on the device running the app,
//  when three simulators share the name "iPhone 17 Pro"). This prints all five.
//
//  Uses `print` rather than `os.Logger` deliberately: the verification recipe
//  captures output with `simctl launch --console-pty`, which sees stdout but NOT
//  the unified log. There are also zero `os.Logger` uses in this codebase, so a
//  Logger here would be the one line you could not read next to the audit's.
//  CLAUDE.md's "never print() in production code" is satisfied by the file-wide
//  `#if DEBUG`, exactly as `ThemeContrastAudit` does it.
//

#if DEBUG

import SwiftUI

@MainActor
enum AppearanceProbe {

    /// One trace. `phase` names the call site so interleaved lines stay attributable.
    static func dump(_ phase: String) {
        let raw = UserDefaults.standard.string(forKey: AppearanceManager.storageKey)
        let parsed = raw.flatMap(AppearanceMode.init(rawValue:))

        print("━━━ APPEARANCE [\(phase)] ━━━")
        print("  stored raw     : \(raw.map { "\"\($0)\"" } ?? "nil (key absent)")")
        print("  parsed         : \(parsed?.rawValue ?? "PARSE MISS → coalesced to .dark")")
        print("  manager.current: \(AppearanceManager.current.rawValue) "
              + "→ override would be \(name(AppearanceManager.current.interfaceStyle))")

        var windows = 0
        for scene in UIApplication.shared.connectedScenes {
            guard let windowScene = scene as? UIWindowScene else { continue }
            print("  scene state=\(windowScene.activationState.rawValue) "
                  + "sceneTrait=\(name(windowScene.traitCollection.userInterfaceStyle))")
            for window in windowScene.windows {
                windows += 1
                let background = UIColor(AppColors.background)
                    .resolvedColor(with: window.traitCollection)
                print("    win \(type(of: window)) key=\(window.isKeyWindow) "
                      + "override=\(name(window.overrideUserInterfaceStyle)) "
                      + "trait=\(name(window.traitCollection.userInterfaceStyle)) "
                      + "background=#\(hex(background))")

                // Walk the modal-presentation chain. A window override does NOT reach a
                // presented controller that carries its own non-`.unspecified` override,
                // and SwiftUI sets exactly that from a `.preferredColorScheme` inside a
                // sheet / fullScreenCover. Screens presented that way (Account is a
                // `.fullScreenCover`) are therefore the one place the app can visibly
                // disagree with the user's choice — so print them, not just the window.
                var controller = window.rootViewController
                var depth = 0
                while let current = controller {
                    let marker = current.overrideUserInterfaceStyle == .unspecified
                        ? "inherits" : "OWN OVERRIDE"
                    print("      vc[\(depth)] \(type(of: current)) "
                          + "override=\(name(current.overrideUserInterfaceStyle)) (\(marker)) "
                          + "trait=\(name(current.traitCollection.userInterfaceStyle))")
                    controller = current.presentedViewController
                    depth += 1
                }
            }
        }
        if windows == 0 {
            print("    (no windows — probe ran before scene attach)")
        }

        // What a token resolves to when NOTHING supplies a style. This is the only
        // line that speaks to the ColorAdaptive `.unspecified` polarity. If the
        // window lines above never show trait=UNSPECIFIED, this value is academic —
        // `.unspecified` is an override INPUT, not a resolved trait.
        let unspecified = UIColor(AppColors.background)
            .resolvedColor(with: UITraitCollection(userInterfaceStyle: .unspecified))
        print("  background @.unspecified = #\(hex(unspecified))   (F4F5F8=light, 171B26=dark)")

        // `configureAppearance()` runs at App.init(), before any window exists. If
        // `UIColor(Color)` had flattened the dynamic provider there, these two would
        // be equal — and the nav bar would be pinned to one appearance for the whole
        // process lifetime, drifting from the page behind it.
        if let navBackground = UINavigationBar.appearance().standardAppearance.backgroundColor {
            let light = navBackground.resolvedColor(with: UITraitCollection(userInterfaceStyle: .light))
            let dark = navBackground.resolvedColor(with: UITraitCollection(userInterfaceStyle: .dark))
            print("  navBar bg      : light=#\(hex(light)) dark=#\(hex(dark))"
                  + (hex(light) == hex(dark) ? "   ⚠️ FLATTENED — nav bar is pinned" : ""))
        }
    }

    /// Pins the tolerant appearance parse. This project has no XCTest target, so
    /// parser contracts are asserted at launch in DEBUG — the same choice
    /// `ThemeContrastAudit.auditHexParsing()` makes for the hex parser.
    static func selfTest() {
        assert(AppearanceMode(tolerantRawValue: "System") == .system)
        assert(AppearanceMode(tolerantRawValue: "system") == .system)
        assert(AppearanceMode(tolerantRawValue: "  SYSTEM \n") == .system)
        assert(AppearanceMode(tolerantRawValue: "dark") == .dark)
        assert(AppearanceMode(tolerantRawValue: "Light") == .light)
        assert(AppearanceMode(tolerantRawValue: "midnight") == nil)
        assert(AppearanceMode(tolerantRawValue: "") == nil)
        // The canonical rawValue must survive the tolerant parse unchanged, or
        // `SettingsSyncManager` would rewrite a good value into a different one.
        for mode in AppearanceMode.allCases {
            assert(AppearanceMode(tolerantRawValue: mode.rawValue) == mode)
        }
    }

    /// Drive the picker's exact code path (`AppearanceManager.set`) through every mode
    /// and dump the resolved state after each, so the Light→Dark→System transitions can
    /// be verified without tapping. Opt-in via
    /// `SIMCTL_CHILD_CAYDEX_APPEARANCE_SELFTEST=1 xcrun simctl launch …`.
    ///
    /// Exists because the interesting bug is a TRANSITION, not a cold-launch state: a
    /// cold launch in any mode looks correct, and only switching between them at
    /// runtime exposes whether the previous mode was actually cleared.
    static func runTransitionSelfTestIfRequested() async {
        guard ProcessInfo.processInfo.environment["CAYDEX_APPEARANCE_SELFTEST"] == "1" else { return }
        let original = AppearanceManager.current
        for mode in [AppearanceMode.light, .dark, .system, .light, .system] {
            AppearanceManager.set(mode)
            try? await Task.sleep(nanoseconds: 1_200_000_000)
            dump("AFTER set→\(mode.rawValue)")
        }
        AppearanceManager.set(original)
    }

    // MARK: - Formatting

    private static func hex(_ color: UIColor) -> String {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        guard color.getRed(&r, green: &g, blue: &b, alpha: &a) else { return "??????" }
        return String(format: "%02X%02X%02X",
                      Int(r * 255 + 0.5), Int(g * 255 + 0.5), Int(b * 255 + 0.5))
    }

    private static func name(_ style: UIUserInterfaceStyle) -> String {
        switch style {
        case .light: return "light"
        case .dark: return "dark"
        case .unspecified: return "UNSPECIFIED"
        @unknown default: return "unknown(\(style.rawValue))"
        }
    }
}

#endif
