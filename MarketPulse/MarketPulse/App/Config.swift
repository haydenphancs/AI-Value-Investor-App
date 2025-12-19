import Foundation

struct Config {
    // MARK: - API Configuration
    static let baseURL = "https://your-api-domain.com/api/v1"

    // MARK: - Supabase Configuration
    static let supabaseURL = "https://your-project.supabase.co"
    static let supabaseAnonKey = "your-supabase-anon-key"

    // MARK: - App Configuration
    static let appName = "MarketPulse"
    static let appVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"

    // MARK: - Pagination
    static let newsPageSize = 20
    static let maxBreakingNews = 10
    static let maxWatchlistPreview = 5
    static let maxReportsPreview = 3

    // MARK: - Timeouts
    static let requestTimeout: TimeInterval = 30
    static let reportGenerationTimeout: TimeInterval = 60
}
