import Foundation
import Combine

struct Config {
    static let baseURL = "http://127.0.0.1:8000/api/v1"

    // MARK: - Supabase Configuration
    static let supabaseURL = "https://gutlnhsjxrkxvrbqbbqq.supabase.co"
    static let supabaseAnonKey = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd1dGxuaHNqeHJreHZyYnFiYnFxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NTk5MTYxMCwiZXhwIjoyMDgxNTY3NjEwfQ.gcDYyWP8rTha0WOKoW1_WZo4pU3hHhRc1yeQLrbTBnQ"


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
