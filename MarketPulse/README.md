# MarketPulse - AI-Powered Value Investing iOS App

A comprehensive SwiftUI iOS application for AI-powered stock research and analysis, built to work with your existing FastAPI backend.

## 📱 Features Implemented (Phase 1 - MVP)

### ✅ Core Screens
1. **Authentication**
   - Login with Supabase integration
   - Token-based authentication with backend

2. **Dashboard**
   - Latest market widget headlines
   - Breaking news feed
   - Watchlist preview (top 5 stocks)
   - Recent research reports preview

3. **News Feed**
   - Paginated news articles with pull-to-refresh
   - AI-generated summaries and bullet points
   - Sentiment filtering (Bullish/Bearish/Neutral)
   - News detail view with related stocks

4. **Stock Search & Detail**
   - Real-time stock search with debouncing
   - Comprehensive stock details (fundamentals, earnings, news)
   - Add/remove from watchlist
   - Generate research reports

5. **Watchlist**
   - User's saved stocks
   - Breaking news badges
   - Swipe-to-delete functionality

6. **Research Reports**
   - List of generated reports with status tracking
   - 5 investor personas (Buffett, Ackman, Munger, Lynch, Graham)
   - Comprehensive report details (thesis, moat, valuation, risks)
   - Report rating system
   - Report generation flow (~30 second process)

7. **User Profile**
   - User info and tier display (Free/Pro/Premium)
   - Usage statistics (research report limits)
   - Activity stats (watchlist count, reports, chats)
   - Sign out functionality

8. **iOS Widget** (Setup guide provided)
   - Home screen widgets (Small/Medium/Large)
   - Auto-updating market insights
   - Deep linking to app

## 🏗️ Architecture

### MVVM Pattern
- **Models**: Codable structs matching backend schemas
- **ViewModels**: ObservableObjects managing state and API calls
- **Views**: SwiftUI components with reactive UI

### Project Structure
```
MarketPulse/
├── App/
│   ├── MarketPulseApp.swift       # App entry point
│   └── Config.swift                 # API configuration
├── Models/
│   ├── Common.swift                 # Enums and base types
│   ├── User.swift
│   ├── Stock.swift
│   ├── News.swift
│   ├── Research.swift
│   ├── Widget.swift
│   ├── Chat.swift
│   └── Education.swift
├── Networking/
│   ├── APIClient.swift              # Core HTTP client
│   ├── APIEndpoint.swift            # API routes
│   ├── APIError.swift               # Error handling
│   └── APIService.swift             # Service layer
├── ViewModels/
│   ├── AuthViewModel.swift
│   ├── DashboardViewModel.swift
│   ├── NewsViewModel.swift
│   ├── StockViewModel.swift
│   ├── WatchlistViewModel.swift
│   ├── ResearchViewModel.swift
│   └── ProfileViewModel.swift
├── Views/
│   ├── Auth/
│   ├── Dashboard/
│   ├── News/
│   ├── Stock/
│   ├── Watchlist/
│   ├── Research/
│   ├── Profile/
│   ├── Components/                  # Reusable UI components
│   └── ContentView.swift            # Main tab navigation
└── Utilities/
    ├── Extensions.swift
    └── Constants.swift
```

## 🚀 Getting Started

### Prerequisites
- Xcode 15.0+
- iOS 15.0+ deployment target
- Active Supabase account
- Running backend API (see `backend/` directory)

### Setup Instructions

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd AI-Value-Investor-App
   ```

2. **Configure API endpoint**

   Edit `MarketPulse/MarketPulse/App/Config.swift`:
   ```swift
   static let baseURL = "https://your-api-domain.com/api/v1"
   static let supabaseURL = "https://your-project.supabase.co"
   static let supabaseAnonKey = "your-supabase-anon-key"
   ```

3. **Install Supabase SDK** (Required for authentication)

   Add Swift Package Dependency:
   - In Xcode: File > Add Package Dependencies
   - Enter: `https://github.com/supabase/supabase-swift`
   - Select version: `2.0.0` or later

4. **Open in Xcode**
   ```bash
   open MarketPulse/MarketPulse.xcodeproj
   ```

5. **Build and Run**
   - Select target: MarketPulse
   - Select simulator or device
   - Press Cmd+R to build and run

### Backend Setup

Ensure your backend is running:
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

## 🔌 API Integration

The app integrates with all Phase 1 MVP endpoints:

### Authentication
- `POST /api/v1/auth/token` - Login with Supabase token
- `POST /api/v1/auth/logout` - Logout
- `GET /api/v1/auth/me` - Get current user

### Stocks
- `GET /api/v1/stocks/search` - Search stocks
- `GET /api/v1/stocks/{ticker}` - Stock details
- `GET /api/v1/stocks/{ticker}/fundamentals` - Fundamentals
- `GET /api/v1/stocks/{ticker}/earnings` - Earnings
- `GET /api/v1/stocks/watchlist/me` - User watchlist
- `POST /api/v1/stocks/watchlist` - Add to watchlist
- `DELETE /api/v1/stocks/watchlist/{id}` - Remove from watchlist

### News
- `GET /api/v1/news/feed` - News feed with pagination
- `GET /api/v1/news/breaking` - Breaking news
- `GET /api/v1/news/{id}` - News detail
- `GET /api/v1/news/stock/{ticker}` - Stock-specific news

### Research
- `POST /api/v1/research/generate` - Generate report
- `GET /api/v1/research/reports` - User's reports
- `GET /api/v1/research/reports/{id}` - Report detail
- `POST /api/v1/research/reports/{id}/rate` - Rate report

### Widget
- `GET /api/v1/widget/latest` - Latest widget update
- `GET /api/v1/widget/timeline` - Widget timeline

### Users
- `GET /api/v1/users/me` - User profile
- `GET /api/v1/users/me/usage` - Usage statistics
- `GET /api/v1/users/me/stats` - User stats

## 📊 Features by User Tier

### Free Tier
- 1 research report per month
- Full news access
- Unlimited watchlist
- Basic profile

### Pro Tier
- 10 research reports per month
- All Free features
- Priority support

### Premium Tier
- Unlimited research reports
- All Pro features
- Advanced analytics (Phase 2)

## 🎨 UI Components

### Reusable Components
- `LoadingView` - Loading states with spinner
- `EmptyStateView` - Empty states with icons and messages
- `SentimentBadge` - Color-coded sentiment indicators
- `StatusBadge` - Report status badges

### Design System
- **Colors**: Sentiment-based (Green/Red/Gray)
- **Spacing**: Consistent padding (8/16/24)
- **Corner Radius**: Rounded corners (8/12/16)
- **Typography**: SF Pro system font

## 🔮 Phase 2 Features (Not Yet Implemented)

Screens prepared but not fully implemented:
1. Chat screens (Session List, Type Selection, Conversation)
2. Education Library (Books, Articles, Content Detail)
3. Advanced Settings
4. Push Notifications
5. Onboarding Flow

Models and networking are ready for Phase 2 implementation.

## 📝 Next Steps

### Immediate TODOs
1. **Integrate Supabase SDK**
   - Replace placeholder auth in `AuthViewModel.swift`
   - Implement actual Supabase sign-in/sign-up

2. **Add Assets**
   - App icon in `Assets.xcassets/AppIcon.appiconset/`
   - Color scheme customization
   - Launch screen design

3. **Test API Integration**
   - Update `Config.swift` with production URLs
   - Test all network calls
   - Handle edge cases and errors

4. **Setup Widget**
   - Follow `WIDGET_SETUP.md` guide
   - Create widget extension target
   - Share code between app and widget

5. **App Store Preparation**
   - Add privacy policy
   - Create screenshots
   - Write app description
   - Configure provisioning profiles

## 🐛 Known Issues & Limitations

1. **Supabase Integration**: Currently uses placeholder token
   - Needs actual Supabase Swift SDK integration
   - Sign-up flow not yet implemented

2. **Image Caching**: AsyncImage doesn't cache
   - Consider adding SDWebImage or Kingfisher

3. **Offline Support**: No offline mode yet
   - Could add local caching with CoreData

4. **Error Handling**: Basic error messages
   - Could improve with specific error screens

## 📚 Resources

- [SwiftUI Documentation](https://developer.apple.com/documentation/swiftui/)
- [Supabase Swift SDK](https://github.com/supabase/supabase-swift)
- [WidgetKit Documentation](https://developer.apple.com/documentation/widgetkit/)
- [Backend API Documentation](../documents/UI_REQUIREMENTS.md)

## 📄 License

[Your License Here]

## 👥 Contributors

[Your Team/Name Here]

---

**Built with ❤️ using SwiftUI**
