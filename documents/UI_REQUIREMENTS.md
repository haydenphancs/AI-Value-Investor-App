# AI Value Investor - Complete UI/UX Requirements for Figma

**Generated:** 2025-12-18
**Purpose:** Backend API analysis for Figma prototype design phase

---

## 📱 SCREEN-BY-SCREEN BREAKDOWN

### 1. **Authentication Screens**

#### 1.1 Login Screen
**Data to Display:** None (handled by Supabase)
**User Actions:**
- "Sign In with Email" → triggers Supabase Auth → calls `POST /api/v1/auth/token` with supabase_token
- "Sign Up" → navigates to sign up flow

**States:**
- Default State
- Loading State (after clicking sign in)
- Error State (invalid credentials)

---

### 2. **Dashboard / Home Screen**

**Data to Display:**
- Widget-style headline (from `GET /api/v1/widget/latest`)
  - headline (string, max 200 chars)
  - sentiment (bullish/bearish/neutral)
  - emoji (1 emoji)
  - daily_trend (string, max 100 chars)
  - market_summary (optional, max 500 chars)
- Breaking News section (from `GET /api/v1/news/breaking`)
  - List of breaking news items (max 10)
  - Each with: news headline, sentiment emoji, stock ticker, stock logo, impact_score
- Quick access to watchlist (top 3-5 stocks)
- Recent research reports (top 2-3)

**User Actions:**
- Pull-to-refresh → triggers `GET /api/v1/widget/latest`
- Tap Breaking News card → navigates to News Detail screen
- Tap Watchlist item → navigates to Stock Detail screen
- Tap Research report → navigates to Research Detail screen
- Tap "See All News" → navigates to News Feed screen
- Tap "See All Watchlist" → navigates to Watchlist screen

**States:**
- Loading State (initial load)
- Empty State (new user, no data)
- Error State (network failure)
- Default State (with data)

---

### 3. **News Feed Screen**

**Data to Display:** (from `GET /api/v1/news/feed`)
- List of news articles (paginated, 20 per page)
- For each article:
  - title (string)
  - ai_summary_bullets (list of 3 bullet points max)
  - sentiment (bullish/bearish/neutral)
  - sentiment_emoji (emoji)
  - published_at (datetime)
  - source_name (string)
  - image_url (optional)

**User Actions:**
- Pull-to-refresh → triggers `GET /api/v1/news/feed`
- Filter by sentiment → triggers `GET /api/v1/news/feed?sentiment=bullish`
- Scroll to bottom → loads next page (pagination)
- Tap article → navigates to News Detail screen
- Mark as read → triggers `POST /api/v1/news/{news_id}/mark-read`

**States:**
- Loading State
- Empty State (no news for filter)
- Default State (with articles)
- Refreshing State (pull-to-refresh)
- Pagination Loading (loading next page)

---

### 4. **News Detail Screen**

**Data to Display:** (from `GET /api/v1/news/{news_id}`)
- title
- image_url (if available)
- published_at
- source_name
- ai_summary (plain English summary)
- ai_summary_bullets (3 bullet points)
- sentiment + sentiment_emoji
- related_stocks (list of stocks mentioned)
  - ticker, company_name, logo_url
- content (optional full article)

**User Actions:**
- Tap stock ticker → navigates to Stock Detail screen
- Share button → iOS share sheet
- Back button → returns to News Feed

**States:**
- Loading State
- Default State (with content)
- Error State (article not found)

---

### 5. **Stock Search Screen**

**Data to Display:** (from `GET /api/v1/stocks/search?query={query}`)
- Search results list (max 10-50)
- For each result:
  - ticker
  - company_name
  - sector
  - market_cap
  - logo_url

**User Actions:**
- Type in search box → triggers `GET /api/v1/stocks/search?query={query}`
- Tap search result → navigates to Stock Detail screen
- Cancel → clears search

**States:**
- Empty State (before search)
- Searching State (API call in progress)
- Results State (with results)
- No Results State (query returned empty)

---

### 6. **Stock Detail Screen**

**Data to Display:**

**From `GET /api/v1/stocks/{ticker}`:**
- ticker, company_name, logo_url
- sector, industry, exchange
- market_cap (formatted)
- description
- website

**From `GET /api/v1/stocks/{ticker}/fundamentals`:**
- List of fundamental data (last 10 periods)
- For each: fiscal_year, fiscal_quarter, revenue, net_income, eps, etc.

**From `GET /api/v1/stocks/{ticker}/earnings`:**
- Upcoming earnings (if upcoming=true)
- earnings_date, eps_estimate, revenue_estimate

**From `GET /api/v1/news/stock/{ticker}`:**
- Recent news (max 20 articles)

**User Actions:**
- "Add to Watchlist" button → triggers `POST /api/v1/stocks/watchlist`
- "Generate Research Report" button → navigates to Research Generation flow
- "Chat About This Stock" button → triggers `POST /api/v1/chat/sessions` then navigates to Chat screen
- Tap on news article → navigates to News Detail screen
- Scroll to view fundamentals (tabs/sections)

**States:**
- Loading State
- Default State (with all data)
- Error State (stock not found)
- Already in Watchlist State (button changes to "Remove from Watchlist")
- Locked State (if free tier user has no credits for research)

---

### 7. **Watchlist Screen**

**Data to Display:** (from `GET /api/v1/stocks/watchlist/me`)
- List of watchlist items
- For each:
  - stock.ticker, stock.company_name, stock.logo_url
  - alert_on_news (boolean)
  - custom_notes (optional)
  - has_breaking_news (boolean flag)
  - added_at (datetime)

**User Actions:**
- Tap stock → navigates to Stock Detail screen
- Swipe to delete → triggers `DELETE /api/v1/stocks/watchlist/{stock_id}`
- Pull-to-refresh → reloads watchlist
- Tap "Edit" → enables multi-select delete mode

**States:**
- Loading State
- Empty State (no stocks in watchlist)
- Default State (with stocks)
- Breaking News Badge State (red badge on stocks with breaking news)

---

### 8. **Research Report List Screen**

**Data to Display:** (from `GET /api/v1/research/reports?limit=20`)
- List of research reports
- For each report:
  - title
  - executive_summary (max 1000 chars)
  - investor_persona (buffett/ackman/munger/lynch/graham)
  - persona_emoji
  - status (pending/processing/completed/failed)
  - stock.ticker, stock.company_name, stock.logo_url
  - created_at
  - user_rating (1-5 stars, optional)

**User Actions:**
- Tap "Generate New Report" → navigates to Research Generation flow
- Tap report → navigates to Research Detail screen
- Pull-to-refresh → reloads reports

**States:**
- Loading State
- Empty State (no reports generated)
- Default State (with reports)
- Locked State (free tier user, no credits remaining - show upgrade prompt)

---

### 9. **Research Report Generation Flow**

#### 9.1 Stock Selection
**Data to Display:**
- Stock search (reuse Stock Search component)

**User Actions:**
- Select stock → proceeds to Persona Selection

#### 9.2 Investor Persona Selection
**Data to Display:**
- List of 5 personas:
  - Warren Buffett (buffett) 🎩
  - Bill Ackman (ackman)
  - Charlie Munger (munger)
  - Peter Lynch (lynch)
  - Benjamin Graham (graham)
- Each with: display_name, description, emoji

**User Actions:**
- Select persona → triggers `POST /api/v1/research/generate`
- Cancel → returns to previous screen

**States:**
- Default State
- Credits Check State (shows remaining credits before generation)
- Locked State (no credits, show upgrade)

#### 9.3 Report Generating Screen
**Data to Display:**
- Stock name and logo
- Selected persona
- Progress indicator (indeterminate, ~30 seconds)
- Status message: "Generating report..."

**User Actions:**
- Wait (background task running)
- Cancel (optional) → navigates back

**States:**
- Generating State (30 seconds)
- Success State → auto-navigates to Report Detail screen
- Failed State → shows error message, "Retry" button

---

### 10. **Research Report Detail Screen**

**Data to Display:** (from `GET /api/v1/research/reports/{report_id}`)

**Report Header:**
- title
- stock.ticker, stock.company_name, stock.logo_url
- investor_persona + persona_emoji
- created_at
- status

**Report Sections:**
- executive_summary
- investment_thesis
  - summary, key_drivers (list), risks (list), time_horizon, conviction_level
- pros (list of strings)
- cons (list of strings)
- moat_analysis
  - moat_rating (wide/narrow/none)
  - moat_sources (list)
  - competitive_position
  - barriers_to_entry (list)
- valuation_analysis
  - valuation_rating (undervalued/fairly-valued/overvalued)
  - key_metrics (dict)
  - margin_of_safety
- risk_assessment
  - overall_risk (low/medium/high)
  - business_risks (list)
  - financial_risks (list)
  - market_risks (list)
- action_recommendation (buy/hold/sell/watch)

**User Actions:**
- Rate report → triggers `POST /api/v1/research/reports/{report_id}/rate`
- Share report → iOS share sheet
- Delete report → triggers `DELETE /api/v1/research/reports/{report_id}`
- View stock → navigates to Stock Detail screen

**States:**
- Loading State
- Default State (completed report)
- Processing State (status=processing, show spinner)
- Failed State (status=failed, show error_message)

---

### 11. **Chat Session List Screen**

**Data to Display:** (from `GET /api/v1/chat/sessions?limit=20`)
- List of chat sessions
- For each:
  - title
  - session_type (education/stock_analysis/general)
  - session_emoji
  - message_count
  - last_message_at
  - preview_message (last message)
  - content.title (if education chat)
  - stock.ticker (if stock analysis chat)

**User Actions:**
- Tap "New Chat" → navigates to Chat Type Selection
- Tap session → navigates to Chat Conversation screen
- Swipe to delete → triggers `DELETE /api/v1/chat/sessions/{session_id}`

**States:**
- Loading State
- Empty State (no chat sessions)
- Default State (with sessions)

---

### 12. **Chat Type Selection Screen**

**Data to Display:**
- 3 options:
  1. "Ask About Education Content" (education)
  2. "Analyze a Stock" (stock_analysis)
  3. "General Questions" (general)

**User Actions:**
- Select "Education" → navigates to Education Content Picker
- Select "Stock Analysis" → navigates to Stock Search (then creates session)
- Select "General" → triggers `POST /api/v1/chat/sessions` → navigates to Chat Conversation

---

### 13. **Chat Conversation Screen**

**Data to Display:** (from `GET /api/v1/chat/sessions/{session_id}`)

**Header:**
- title
- session_type
- content.title or stock.ticker (if applicable)

**Messages:** (list of messages)
- For each message:
  - role (user/assistant)
  - content
  - created_at
  - citations (for assistant messages, list of sources)

**User Actions:**
- Type message → triggers `POST /api/v1/chat/sessions/{session_id}/messages`
- Send button → sends message, waits for AI response
- Tap citation → shows source details (modal or link)
- Delete session → triggers `DELETE /api/v1/chat/sessions/{session_id}`

**States:**
- Loading State (loading conversation history)
- Empty State (no messages yet)
- Default State (with messages)
- AI Typing State (waiting for response, show typing indicator)
- Error State (message send failed)

---

### 14. **Education Library Screen**

**Data to Display:** (from `GET /api/v1/education/content?limit=20`)
- Tabs: "All", "Books", "Articles"
- List of educational content
- For each:
  - type (book/article)
  - title
  - author
  - publication_year
  - summary (truncated, max 500 chars)
  - cover_image_url (optional)
  - chunk_count (number of indexed chunks)
  - topics (list of strings)

**User Actions:**
- Tap "Books" tab → triggers `GET /api/v1/education/books`
- Tap "Articles" tab → triggers `GET /api/v1/education/articles`
- Search → triggers `GET /api/v1/education/content?search={query}`
- Tap content → navigates to Education Content Detail screen
- Tap "Chat" button → creates education chat session → navigates to Chat Conversation

**States:**
- Loading State
- Empty State (no content)
- Default State (with content)
- Search Results State

---

### 15. **Education Content Detail Screen**

**Data to Display:** (from `GET /api/v1/education/content/{content_id}`)
- title
- author
- publication_year
- summary (full)
- topics (list)
- source_url (if article)
- cover_image_url
- chunk_count
- is_processed (boolean)

**User Actions:**
- "Start Chat" button → triggers `POST /api/v1/chat/sessions` → navigates to Chat Conversation
- "Read Full Text" (if available) → shows full_text
- Back button → returns to Education Library

**States:**
- Loading State
- Default State
- Processing State (if is_processed=false, show "Content being indexed...")

---

### 16. **User Profile / Settings Screen**

**Data to Display:**

**From `GET /api/v1/users/me`:**
- email
- full_name
- tier (free/pro/premium)
- tier badge/icon

**From `GET /api/v1/users/me/usage`:**
- deep_research.used
- deep_research.limit
- deep_research.remaining (or "unlimited")
- reset_at (date)

**From `GET /api/v1/users/me/stats`:**
- watchlist_count
- reports_generated
- chat_sessions
- last_activity

**User Actions:**
- "Edit Profile" → opens Edit Profile modal
- "Upgrade Tier" → navigates to Upgrade/Payment screen (not in backend yet)
- "Notification Settings" → opens settings modal
- "Sign Out" → triggers `POST /api/v1/auth/logout`
- "Delete Account" → triggers `DELETE /api/v1/users/me`

**States:**
- Loading State
- Default State
- Free Tier State (show upgrade prompt)
- Pro/Premium State (show usage bars)

---

### 17. **Widget (iOS Home Screen)**

**Data to Display:** (from `GET /api/v1/widget/latest`)
- headline (max 200 chars)
- sentiment (bullish/bearish/neutral)
- emoji
- daily_trend (max 100 chars)
- published_at

**User Actions:**
- Tap widget → deep links into app (possibly to linked report or dashboard)

**States:**
- Default State (with latest update)
- Empty State (new user, no widget data)
- Stale State (no update in 24+ hours)

**Widget Timeline:** (from `GET /api/v1/widget/timeline?hours=24`)
- iOS WidgetKit supports scheduling multiple updates
- Backend provides past_updates and future_updates

---

## 🎨 CROSS-CUTTING UI STATES

These states apply to MULTIPLE screens:

### Loading State
- Spinner or skeleton screens
- Appears on: ALL screens during initial data fetch

### Empty State
- No data available
- Custom message per screen:
  - Watchlist: "Your watchlist is empty. Search for stocks to add."
  - Reports: "No reports yet. Generate your first analysis."
  - News: "No news available."
  - Chat: "Start a conversation."

### Error State
- Network error, 500 error, etc.
- Retry button
- Error message

### Locked State (Free Tier)
- User has hit monthly limit for deep research
- Show upgrade prompt
- Appears on:
  - Research Report List (when credits = 0)
  - Stock Detail (when trying to generate report)
  - Research Generation Flow

### Refresh State
- Pull-to-refresh indicator
- Appears on: News Feed, Dashboard, Watchlist, Research Reports

### Pagination Loading
- Loading indicator at bottom of list
- Appears on: News Feed, Education Library

---

## 🔔 ADDITIONAL UI COMPONENTS

### Navigation Bar
**Tabs:**
1. Dashboard (home icon)
2. News (newspaper icon)
3. Search (magnifying glass)
4. Education (book icon)
5. Profile (person icon)

### Alerts / Toasts
- Success: "Added to watchlist", "Report generated"
- Error: "Network error", "Failed to load"
- Info: "Generating report... (~30s)"

### Modals
- Rate Report (1-5 stars + optional feedback)
- Delete Confirmation
- Edit Profile
- Notification Settings

### Legal Disclaimer Banner
- Required on screens showing analysis (from `GET /api/v1/disclaimer`)
- Shows: "This is for educational purposes only. Not financial advice."

---

## 📊 SENTIMENT INDICATORS

Sentiment appears throughout the app (news, widgets, reports). Design consistent visual language:
- **Bullish** → Green + 📈 or 🟢
- **Bearish** → Red + 📉 or 🔴
- **Neutral** → Gray/Yellow + ➖ or 🟡

---

## 🔐 TIER-SPECIFIC UI VARIATIONS

### Free Tier Users
- Deep Research limit: 1/month
- Show "Upgrade" prompts on locked features
- Usage bar visible on Profile screen

### Pro Tier Users
- Deep Research limit: 10/month
- Usage bar shows 3/10, etc.

### Premium Tier Users
- Deep Research: Unlimited (show "unlimited" badge)
- No upgrade prompts

---

## 🧭 DEEP LINKING SUPPORT

Widget deep links (from `deep_link_url` in widget_updates):
- Tap widget → opens app to specific screen (Dashboard or Research Report Detail)

---

## ✅ SUMMARY: SCREENS TO DESIGN IN FIGMA

1. Login Screen
2. Dashboard / Home Screen
3. News Feed Screen
4. News Detail Screen
5. Stock Search Screen
6. Stock Detail Screen
7. Watchlist Screen
8. Research Report List Screen
9. Research Generation Flow (3 sub-screens: Stock Select, Persona Select, Generating)
10. Research Report Detail Screen
11. Chat Session List Screen
12. Chat Type Selection Screen
13. Chat Conversation Screen
14. Education Library Screen
15. Education Content Detail Screen
16. User Profile / Settings Screen
17. iOS Widget (Small, Medium, Large sizes)

**Plus UI States:**
- Loading States (all screens)
- Empty States (all applicable screens)
- Error States (all screens)
- Locked States (Research screens)
- Tier-specific variations (Profile, Research)

---

## 📝 API REFERENCE

All endpoints are prefixed with `/api/v1/`

### Authentication
- `POST /auth/token` - Exchange Supabase token
- `POST /auth/refresh` - Refresh access token
- `POST /auth/logout` - Logout user
- `GET /auth/me` - Get current user info
- `POST /auth/verify` - Verify token

### Users
- `GET /users/me` - Get user profile
- `PATCH /users/me` - Update user profile
- `GET /users/me/usage` - Get usage statistics
- `GET /users/me/stats` - Get comprehensive stats
- `DELETE /users/me` - Delete account

### Stocks
- `GET /stocks/search?query={query}` - Search stocks
- `GET /stocks/{ticker}` - Get stock details
- `GET /stocks/{ticker}/fundamentals` - Get fundamentals
- `GET /stocks/{ticker}/earnings` - Get earnings data
- `GET /stocks/watchlist/me` - Get user watchlist
- `POST /stocks/watchlist` - Add to watchlist
- `DELETE /stocks/watchlist/{stock_id}` - Remove from watchlist

### News
- `GET /news/feed` - Get news feed (with pagination)
- `GET /news/breaking` - Get breaking news
- `GET /news/{news_id}` - Get news detail
- `GET /news/stock/{ticker}` - Get stock-specific news
- `POST /news/{news_id}/mark-read` - Mark as read

### Research
- `POST /research/generate` - Generate research report
- `GET /research/reports` - Get user's reports
- `GET /research/reports/{report_id}` - Get report detail
- `POST /research/reports/{report_id}/rate` - Rate report
- `DELETE /research/reports/{report_id}` - Delete report

### Chat
- `POST /chat/sessions` - Create chat session
- `GET /chat/sessions` - Get user's sessions
- `GET /chat/sessions/{session_id}` - Get session with messages
- `POST /chat/sessions/{session_id}/messages` - Send message
- `DELETE /chat/sessions/{session_id}` - Delete session

### Widget
- `GET /widget/latest` - Get latest widget update
- `GET /widget/timeline?hours=24` - Get widget timeline
- `GET /widget/history` - Get widget history
- `GET /widget/{update_id}` - Get specific update
- `POST /widget/generate` - Manually generate update

### Education
- `GET /education/content` - Browse all content
- `GET /education/content/{content_id}` - Get content details
- `GET /education/books` - Get books
- `GET /education/articles` - Get articles
- `GET /education/topics` - Get available topics
- `POST /education/content/{content_id}/favorite` - Favorite content
- `GET /education/search?query={query}` - Semantic search

### System
- `GET /` - API health check
- `GET /health` - Detailed health check
- `GET /disclaimer` - Legal disclaimer

---

## 🎯 DESIGN PRIORITIES

### High Priority (MVP)
1. Login Screen
2. Dashboard
3. News Feed
4. Stock Search & Detail
5. Watchlist
6. Research Report (List & Detail)
7. User Profile
8. Widget

### Medium Priority
9. Chat Screens
10. Education Library

### Low Priority (Post-MVP)
11. Advanced Settings
12. Notifications Center
13. Analytics Dashboard

---

**End of Document**
