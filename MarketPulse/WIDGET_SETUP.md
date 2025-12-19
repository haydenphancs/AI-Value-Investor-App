# iOS Widget Setup Guide (WidgetKit)

## Overview
The MarketPulse iOS Widget displays the latest market insights from the API on the user's home screen.

## Setup Instructions

### 1. Add Widget Extension to Xcode Project

1. Open `MarketPulse.xcodeproj` in Xcode
2. Go to **File > New > Target**
3. Select **Widget Extension**
4. Name it `MarketPulseWidget`
5. Click **Finish** (do NOT activate the scheme)

### 2. Widget Implementation

Create the following files in the `MarketPulseWidget` folder:

#### MarketPulseWidget.swift
```swift
import WidgetKit
import SwiftUI

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> WidgetEntry {
        WidgetEntry(date: Date(), widgetUpdate: sampleWidget)
    }

    func getSnapshot(in context: Context, completion: @escaping (WidgetEntry) -> ()) {
        let entry = WidgetEntry(date: Date(), widgetUpdate: sampleWidget)
        completion(entry)
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<Entry>) -> ()) {
        Task {
            let entries: [WidgetEntry] = await fetchWidgetData()
            let timeline = Timeline(entries: entries, policy: .after(Date().addingTimeInterval(3600)))
            completion(timeline)
        }
    }

    private func fetchWidgetData() async -> [WidgetEntry] {
        do {
            let apiService = APIService()
            let widget = try await apiService.getWidgetLatest()
            return [WidgetEntry(date: Date(), widgetUpdate: widget)]
        } catch {
            return [WidgetEntry(date: Date(), widgetUpdate: sampleWidget)]
        }
    }

    private var sampleWidget: WidgetUpdate {
        WidgetUpdate(
            id: "sample",
            headline: "Market Update",
            sentiment: .neutral,
            emoji: "📊",
            dailyTrend: "Markets steady today",
            marketSummary: nil,
            publishedAt: Date(),
            deepLinkUrl: nil,
            linkedReportId: nil,
            createdAt: Date()
        )
    }
}

struct WidgetEntry: TimelineEntry {
    let date: Date
    let widgetUpdate: WidgetUpdate
}

struct MarketPulseWidgetEntryView : View {
    @Environment(\.widgetFamily) var family
    var entry: Provider.Entry

    var body: some View {
        switch family {
        case .systemSmall:
            SmallWidgetView(widget: entry.widgetUpdate)
        case .systemMedium:
            MediumWidgetView(widget: entry.widgetUpdate)
        case .systemLarge:
            LargeWidgetView(widget: entry.widgetUpdate)
        default:
            SmallWidgetView(widget: entry.widgetUpdate)
        }
    }
}

@main
struct MarketPulseWidget: Widget {
    let kind: String = "MarketPulseWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: Provider()) { entry in
            MarketPulseWidgetEntryView(entry: entry)
        }
        .configurationDisplayName("MarketPulse")
        .description("Stay updated with AI-powered market insights")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}
```

#### WidgetViews.swift
```swift
import SwiftUI
import WidgetKit

struct SmallWidgetView: View {
    let widget: WidgetUpdate

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(widget.emoji)
                    .font(.title)

                Spacer()

                SentimentBadge(sentiment: widget.sentiment)
            }

            Text(widget.headline)
                .font(.caption)
                .fontWeight(.bold)
                .lineLimit(3)

            Spacer()

            Text(widget.publishedAt.timeAgo())
                .font(.caption2)
                .foregroundColor(.secondary)
        }
        .padding()
    }
}

struct MediumWidgetView: View {
    let widget: WidgetUpdate

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(widget.emoji)
                    .font(.largeTitle)

                Spacer()

                SentimentBadge(sentiment: widget.sentiment)
            }

            Text(widget.headline)
                .font(.subheadline)
                .fontWeight(.bold)
                .lineLimit(2)

            Text(widget.dailyTrend)
                .font(.caption)
                .foregroundColor(.secondary)
                .lineLimit(2)

            Spacer()

            Text(widget.publishedAt.timeAgo())
                .font(.caption2)
                .foregroundColor(.secondary)
        }
        .padding()
    }
}

struct LargeWidgetView: View {
    let widget: WidgetUpdate

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(widget.emoji)
                    .font(.largeTitle)

                Spacer()

                SentimentBadge(sentiment: widget.sentiment)
            }

            Text(widget.headline)
                .font(.headline)
                .fontWeight(.bold)
                .lineLimit(3)

            Text(widget.dailyTrend)
                .font(.subheadline)
                .foregroundColor(.secondary)
                .lineLimit(3)

            if let summary = widget.marketSummary {
                Text(summary)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(4)
            }

            Spacer()

            HStack {
                Text("MarketPulse")
                    .font(.caption2)
                    .foregroundColor(.secondary)

                Spacer()

                Text(widget.publishedAt.timeAgo())
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding()
    }
}
```

### 3. Share Code Between App and Widget

1. Select files to share (Models, Networking, etc.)
2. In **File Inspector**, check both **MarketPulse** and **MarketPulseWidget** under **Target Membership**

Required shared files:
- All files in `Models/`
- All files in `Networking/`
- `Config.swift`
- `Extensions.swift`
- `Constants.swift`
- `SentimentBadge.swift` component

### 4. Configure Widget Deep Linking

In `MarketPulseApp.swift`, add:

```swift
import SwiftUI

@main
struct MarketPulseApp: App {
    @StateObject private var authViewModel = AuthViewModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(authViewModel)
                .onOpenURL { url in
                    handleDeepLink(url)
                }
        }
    }

    private func handleDeepLink(_ url: URL) {
        // Handle widget deep links
        // Example: marketpulse://report/123
        if url.scheme == "marketpulse" {
            if url.host == "report", let reportId = url.pathComponents.dropFirst().first {
                // Navigate to research report
                print("Opening report: \(reportId)")
            }
        }
    }
}
```

### 5. Test the Widget

1. Build and run the **MarketPulse** app on a simulator or device
2. Go to the iOS home screen
3. Long press on the home screen
4. Tap the **+** button
5. Search for "MarketPulse"
6. Add the widget in your preferred size

## Widget Features

- **Small**: Headline + Sentiment
- **Medium**: Headline + Daily Trend + Sentiment
- **Large**: Full update with market summary

## Refresh Schedule

- Auto-refreshes every hour
- Uses `Timeline` with `.after()` policy
- Background fetch handled by iOS

## Notes

- Widgets require iOS 14+
- Network requests limited in widget context
- Consider caching widget data in UserDefaults
- Test with different time zones and locales
