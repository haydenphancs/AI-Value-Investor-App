import SwiftUI

/// App-wide font definitions following iOS Human Interface Guidelines
enum AppFonts {

    // MARK: - Title Fonts

    /// Large title - 28pt Bold
    static let largeTitle = Font.system(size: 28, weight: .bold)

    /// Title 1 - 24pt Bold
    static let title1 = Font.system(size: 24, weight: .bold)

    /// Title 2 - 20pt Semibold
    static let title2 = Font.system(size: 20, weight: .semibold)

    /// Title 3 - 18pt Semibold
    static let title3 = Font.system(size: 18, weight: .semibold)

    // MARK: - Body Fonts

    /// Headline - 17pt Semibold
    static let headline = Font.system(size: 17, weight: .semibold)

    /// Body - 17pt Regular
    static let body = Font.system(size: 17, weight: .regular)

    /// Callout - 16pt Regular
    static let callout = Font.system(size: 16, weight: .regular)

    /// Subheadline - 15pt Regular
    static let subheadline = Font.system(size: 15, weight: .regular)

    // MARK: - Caption Fonts

    /// Footnote - 13pt Regular
    static let footnote = Font.system(size: 13, weight: .regular)

    /// Caption 1 - 12pt Regular
    static let caption1 = Font.system(size: 12, weight: .regular)

    /// Caption 2 - 11pt Regular
    static let caption2 = Font.system(size: 11, weight: .regular)

    // MARK: - Specialized Fonts

    /// Tab label font - 14pt Medium
    static let tabLabel = Font.system(size: 14, weight: .medium)

    /// Chart axis label - 11pt Regular
    static let chartAxis = Font.system(size: 11, weight: .regular)

    /// Percentage display - 12pt Semibold
    static let percentage = Font.system(size: 12, weight: .semibold)

    /// Section header - 20pt Bold
    static let sectionHeader = Font.system(size: 20, weight: .bold)

    /// Detail link - 16pt Semibold
    static let detailLink = Font.system(size: 16, weight: .semibold)
}
