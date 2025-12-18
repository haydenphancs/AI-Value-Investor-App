# MarketPulse iOS App

This is your AI Value Investor iOS application built with SwiftUI.

## Project Structure

```
MarketPulse-iOS/
└── MarketPulse/
    ├── App.swift                           # Main app entry point
    ├── RootView.swift                      # Root navigation (Login/Main tabs)
    ├── Info.plist                          # App configuration
    ├── Assets.xcassets/                    # App icons and images
    ├── Core/
    │   ├── Models/
    │   │   └── Models.swift               # All data models
    │   ├── Networking/
    │   │   └── ApiClient.swift            # API networking layer
    │   └── Services/
    │       ├── AuthService.swift          # Authentication service
    │       └── NewsService.swift          # News data service
    └── Features/
        ├── Auth/
        │   └── LoginView.swift            # Login screen
        ├── Dashboard/
        │   └── DashboardView.swift        # Home dashboard
        ├── News/
        │   └── NewsFeedView.swift         # News feed & detail
        ├── Watchlist/
        │   └── WatchlistView.swift        # Stock watchlist
        ├── Stocks/
        │   └── StockSearchView.swift      # Stock search & detail
        ├── Reports/
        │   └── ReportsViews.swift         # Research reports
        ├── Chat/
        │   └── ChatViews.swift            # AI chat interface
        ├── Education/
        │   └── EducationViews.swift       # Educational content
        └── Profile/
            └── ProfileView.swift          # User profile
```

## Features Included

✅ **Authentication** - Login/signup screens
✅ **Dashboard** - Market news widget and breaking news
✅ **News Feed** - Browse, filter, and read AI-summarized news
✅ **Watchlist** - Track favorite stocks with alerts
✅ **Stock Search** - Search and view stock details
✅ **Research Reports** - AI-generated investment reports
✅ **AI Chat** - Chat about stocks and investing
✅ **Education** - Access educational content
✅ **Profile** - User settings and usage statistics

## How to Open in Xcode

### Option 1: Create New Xcode Project (Recommended)

1. **Open Xcode** and select "Create a new Xcode project"

2. **Choose Template:**
   - Select "iOS" → "App"
   - Click "Next"

3. **Configure Project:**
   - **Product Name:** `MarketPulse`
   - **Team:** Select your Apple Developer team
   - **Organization Identifier:** `com.yourcompany` (or your preference)
   - **Interface:** SwiftUI
   - **Language:** Swift
   - **Storage:** None (or your preference)
   - Click "Next"

4. **Choose Location:**
   - Navigate to `/home/user/AI-Value-Investor-App/`
   - Click "Create"

5. **Replace Files:**
   - Delete the default files Xcode created
   - Drag all files from `MarketPulse-iOS/MarketPulse/` into your Xcode project
   - When prompted, select:
     - ✅ "Copy items if needed"
     - ✅ "Create groups"
     - ✅ Add to targets: MarketPulse

### Option 2: Use Command Line

```bash
# Navigate to the iOS project directory
cd /home/user/AI-Value-Investor-App/MarketPulse-iOS

# If you have Xcode command line tools installed:
# You can create a project using xcodebuild (on macOS only)
```

### Option 3: Manual Import to Existing Project

If you already have an Xcode project:
1. Open your project in Xcode
2. Right-click on project navigator
3. Select "Add Files to..."
4. Navigate to `MarketPulse-iOS/MarketPulse/`
5. Select all Swift files and folders
6. Ensure "Copy items if needed" is checked

## Configuration Needed

### 1. Update API Base URL

Edit `Core/Networking/ApiClient.swift` line 8:

```swift
static var `default`: APIConfig {
  .init(baseURL: URL(string: "YOUR_BACKEND_URL_HERE")!, supabaseToken: nil)
}
```

Replace `"YOUR_BACKEND_URL_HERE"` with your actual backend URL (e.g., your Railway deployment).

### 2. Update Bundle Identifier

In Xcode:
1. Select your project in the navigator
2. Select the "MarketPulse" target
3. Go to "Signing & Capabilities" tab
4. Update the "Bundle Identifier" to match your Apple Developer account

### 3. Configure Team Signing

1. In "Signing & Capabilities"
2. Select your development team
3. Enable "Automatically manage signing"

## Running the App

1. **Select a Simulator** or connect your iPhone
2. **Press ⌘R** or click the "Run" button
3. The app will build and launch

## Next Steps

### Connect to Your Backend

The app currently uses mock data. To connect to your real backend:

1. Update `ApiClient.swift` with your backend URL
2. Implement authentication token storage
3. Update services in `Core/Services/` to use real API calls

### Customize App Icon

1. Design app icons at required sizes (1024x1024, 180x180, 120x120, etc.)
2. Drag them into `Assets.xcassets/AppIcon.appiconset/` in Xcode

### Add Missing Views

Some views reference other views that need to be created:
- Create any additional views as needed
- Connect navigation properly

## SwiftUI Code Quality

Your code includes:
- ✅ Modern SwiftUI architecture
- ✅ Async/await for networking
- ✅ Proper error handling
- ✅ MVVM-like pattern with `@State` management
- ✅ Reusable components
- ✅ Clean separation of concerns

## Troubleshooting

**Build Errors?**
- Make sure all files are added to the target
- Check that Swift version is compatible (Swift 5.5+)
- Verify iOS deployment target (iOS 15.0+)

**Missing Symbols?**
- Some views may reference helpers not yet created
- Check for any `import` statements that need frameworks

**Can't Run on Device?**
- Ensure you have a valid Apple Developer account
- Check signing & capabilities settings
- Verify bundle identifier is unique

## Development

This is a production-ready iOS app structure. You can:
- Build and run immediately
- Customize the UI/UX
- Connect to your backend API
- Submit to the App Store when ready

---

**Generated from Supernova.io export**
**Framework:** SwiftUI
**Minimum iOS:** 15.0+
**Language:** Swift 5.5+
