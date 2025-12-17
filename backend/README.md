# AI Value Investor - FastAPI Backend

Production-grade FastAPI backend for the AI Value Investor iOS application. This backend provides AI-powered financial analysis, news aggregation, and educational content delivery.

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Setup Instructions](#setup-instructions)
- [API Documentation](#api-documentation)
- [Security](#security)
- [Development](#development)
- [Deployment](#deployment)

## Architecture Overview

The backend follows a layered architecture:

```
┌─────────────────────────────────────────────┐
│           iOS Client (SwiftUI)              │
└─────────────────┬───────────────────────────┘
                  │ HTTPS/JSON
┌─────────────────▼───────────────────────────┐
│         FastAPI Application Layer           │
│  ┌─────────────────────────────────────┐   │
│  │  API Routers (v1)                   │   │
│  │  - Auth, Users, Stocks, News        │   │
│  │  - Research, Chat, Widget, Education│   │
│  └─────────────────────────────────────┘   │
│  ┌─────────────────────────────────────┐   │
│  │  Service Layer                      │   │
│  │  - Business Logic                   │   │
│  │  - AI Agents                        │   │
│  └─────────────────────────────────────┘   │
└─────────────────┬───────────────────────────┘
                  │
    ┌─────────────┼─────────────┐
    │             │             │
┌───▼───┐   ┌─────▼─────┐   ┌─▼────┐
│Supabase│   │ Gemini AI │   │ FMP  │
│(Postgres)│   │    API    │   │ API  │
└────────┘   └───────────┘   └──────┘
```

## Features

### Core Features (Based on Requirements Document)

✅ **Automated News Summarization** (Section 4.1)
- Aggregate news from multiple sources
- AI-powered sentiment analysis (bullish/bearish/neutral)
- Plain English summaries (3 bullet points max)
- Breaking news alerts for watchlist stocks

✅ **Live Widget Support** (Section 4.2)
- iOS WidgetKit timeline updates
- Deep linking support
- Pre-market and post-market updates

✅ **Deep Research Agents** (Section 4.3)
- Investor persona simulation (Buffett, Ackman, Munger, Lynch, Graham)
- Large context window analysis using Gemini 1.5 Pro+
- Comprehensive company reports (pros, cons, moat analysis)
- Usage limits per tier (Free: 1, Pro: 10, Premium: unlimited)

✅ **Educational Content Chat** (Section 4.4)
- RAG-based chat with investment books and articles
- Vector similarity search using pgvector
- Citation support

✅ **Company Fundamentals** (Section 4.4)
- Financial data from FMP API
- Interactive AI chat about company data
- Charts and insights

### Technical Features

- **Authentication**: JWT + Supabase Auth integration
- **Authorization**: Role-based access control (Free/Pro/Premium tiers)
- **Rate Limiting**: Per-user request throttling
- **Caching**: Redis-based caching for performance
- **Background Jobs**: APScheduler for scheduled tasks
- **Vector Search**: pgvector for RAG features
- **API Versioning**: V1 API with future extensibility

## Tech Stack

### Core Framework
- **FastAPI**: Modern, high-performance web framework
- **Python 3.11+**: Programming language
- **Uvicorn**: ASGI server

### Database & Storage
- **Supabase**: PostgreSQL with pgvector extension
- **Redis**: Caching and background job queue

### AI & Machine Learning
- **Google Gemini 1.5 Pro+**: Large language model
- **LangChain**: AI agent framework
- **Sentence Transformers**: Text embeddings

### External APIs
- **Financial Modeling Prep (FMP)**: Financial data
- **NewsAPI / SerpApi**: News aggregation
- **Supabase Auth**: User authentication

### Security
- **python-jose**: JWT token handling
- **passlib**: Password hashing
- **Row Level Security**: Supabase RLS policies

## Project Structure

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application entry point
│   ├── config.py            # Configuration management
│   ├── database.py          # Database connections
│   ├── dependencies.py      # FastAPI dependencies
│   │
│   ├── api/                 # API endpoints
│   │   └── v1/
│   │       ├── api.py       # Router aggregation
│   │       └── endpoints/   # Individual endpoint modules
│   │           ├── auth.py
│   │           ├── users.py
│   │           ├── stocks.py
│   │           ├── news.py
│   │           ├── research.py
│   │           ├── chat.py
│   │           ├── widget.py
│   │           └── education.py
│   │
│   ├── core/                # Core utilities
│   │   ├── security.py      # Auth & security
│   │   └── middleware.py    # Custom middleware
│   │
│   ├── models/              # Database models (SQLAlchemy)
│   │   └── [pending]
│   │
│   ├── schemas/             # Pydantic schemas
│   │   └── [pending]
│   │
│   ├── services/            # Business logic
│   │   ├── news_service.py
│   │   ├── research_service.py
│   │   ├── chat_service.py
│   │   ├── widget_service.py
│   │   └── user_service.py
│   │
│   ├── integrations/        # External API clients
│   │   ├── gemini.py        # Google Gemini
│   │   ├── fmp.py           # Financial Modeling Prep
│   │   ├── news_api.py      # News aggregation
│   │   └── supabase_client.py
│   │
│   └── agents/              # AI agents
│       ├── news_summarizer.py
│       ├── research_agent.py
│       └── education_agent.py
│
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variables template
└── README.md               # This file
```

## Setup Instructions

### Prerequisites

- Python 3.11 or higher
- PostgreSQL (via Supabase)
- Redis server
- API Keys:
  - Supabase account & project
  - Google Gemini API key
  - Financial Modeling Prep API key
  - NewsAPI.org key (optional)
  - SerpApi key (optional)

### Installation

1. **Clone the repository**
   ```bash
   cd AI-Value-Investor-App/backend
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env and fill in your API keys
   ```

5. **Initialize database**
   - Run the database schema from `/database/supabase_schema.sql` in your Supabase SQL editor
   - Enable required extensions (uuid-ossp, pgvector)

6. **Start Redis** (if running locally)
   ```bash
   redis-server
   ```

7. **Run the application**
   ```bash
   # Development mode
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

   # Or using Python directly
   python -m app.main
   ```

8. **Access the API**
   - API: http://localhost:8000
   - Documentation: http://localhost:8000/api/docs (Swagger UI)
   - Alternative docs: http://localhost:8000/api/redoc

## API Documentation

### Base URL
```
http://localhost:8000/api/v1
```

### Authentication

All protected endpoints require a Bearer token in the Authorization header:

```bash
Authorization: Bearer <your-jwt-token>
```

### Main Endpoints

#### Authentication (`/auth`)
- `POST /auth/token` - Exchange Supabase token for app token
- `POST /auth/refresh` - Refresh access token
- `GET /auth/me` - Get current user info
- `POST /auth/logout` - Logout

#### Users (`/users`)
- `GET /users/me` - Get user profile
- `PATCH /users/me` - Update profile
- `GET /users/me/usage` - Get usage statistics
- `DELETE /users/me` - Delete account

#### Stocks (`/stocks`)
- `GET /stocks/search` - Search stocks
- `GET /stocks/{ticker}` - Get stock details
- `GET /stocks/{ticker}/fundamentals` - Get financial data
- `GET /stocks/watchlist/me` - Get user's watchlist
- `POST /stocks/watchlist` - Add to watchlist
- `DELETE /stocks/watchlist/{stock_id}` - Remove from watchlist

#### News (`/news`)
- `GET /news/feed` - Get news feed
- `GET /news/breaking` - Get breaking news
- `GET /news/{news_id}` - Get news detail
- `GET /news/stock/{ticker}` - Get news for stock

#### Research (`/research`)
- `POST /research/generate` - Generate deep research report
- `GET /research/reports` - Get user's reports
- `GET /research/reports/{report_id}` - Get report details
- `POST /research/reports/{report_id}/rate` - Rate a report

#### Chat (`/chat`)
- `POST /chat/sessions` - Create chat session
- `GET /chat/sessions` - Get user's chat sessions
- `POST /chat/sessions/{session_id}/messages` - Send message
- `GET /chat/sessions/{session_id}` - Get session details

#### Widget (`/widget`)
- `GET /widget/latest` - Get latest widget update
- `GET /widget/timeline` - Get widget timeline
- `GET /widget/history` - Get widget history

#### Education (`/education`)
- `GET /education/content` - Browse educational content
- `GET /education/books` - Get investment books
- `GET /education/articles` - Get investment articles
- `GET /education/search` - Semantic search

### Example Requests

**Search for a stock:**
```bash
curl -X GET "http://localhost:8000/api/v1/stocks/search?query=AAPL" \
  -H "Authorization: Bearer <token>"
```

**Generate deep research report:**
```bash
curl -X POST "http://localhost:8000/api/v1/research/generate" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "stock_id": "uuid-here",
    "investor_persona": "buffett",
    "analysis_period": "annual"
  }'
```

## Security

### Security Measures (Section 5.3)

1. **API Keys**: All API keys are server-side only, never exposed to clients
2. **Row Level Security**: Supabase RLS policies protect user data
3. **JWT Authentication**: Secure token-based authentication
4. **Password Hashing**: bcrypt for password storage
5. **HTTPS Only**: All production traffic over HTTPS
6. **Rate Limiting**: Per-user request throttling
7. **CORS**: Configured for production origins only

### Environment Variables

**CRITICAL**: Never commit `.env` file to version control!

The `.env` file contains sensitive API keys and secrets. Always use `.env.example` as a template.

## Development

### Running Tests
```bash
pytest
```

### Code Formatting
```bash
black app/
flake8 app/
```

### Type Checking
```bash
mypy app/
```

### Local Development

1. Enable debug mode in `.env`:
   ```
   DEBUG=true
   ENVIRONMENT=development
   ```

2. Use hot reload:
   ```bash
   uvicorn app.main:app --reload
   ```

3. Access debug endpoints:
   - `/debug/config` - View configuration (debug mode only)

## Deployment

### Production Checklist

- [ ] Set `DEBUG=false` in environment
- [ ] Set `ENVIRONMENT=production`
- [ ] Update `ALLOWED_ORIGINS` to your iOS app domain
- [ ] Use strong `SECRET_KEY` (generate with `openssl rand -hex 32`)
- [ ] Enable HTTPS only
- [ ] Set up proper logging
- [ ] Configure Redis for production
- [ ] Set up monitoring (e.g., Sentry)
- [ ] Review and enable Supabase RLS policies
- [ ] Set up automatic backups
- [ ] Configure rate limiting appropriately

### Deployment Options

1. **Docker** (recommended)
   ```bash
   docker build -t ai-value-investor-api .
   docker run -p 8000:8000 --env-file .env ai-value-investor-api
   ```

2. **Cloud Platforms**
   - AWS ECS/Fargate
   - Google Cloud Run
   - Heroku
   - DigitalOcean App Platform
   - Railway

3. **Systemd Service** (Linux)
   ```ini
   [Unit]
   Description=AI Value Investor API
   After=network.target

   [Service]
   Type=simple
   User=www-data
   WorkingDirectory=/path/to/backend
   Environment="PATH=/path/to/venv/bin"
   ExecStart=/path/to/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

## Performance Optimizations

- **Database Connection Pooling**: Configured for optimal performance
- **Async Operations**: All I/O operations are async
- **Caching**: Redis caching for expensive operations
- **Response Compression**: GZip middleware enabled
- **Query Optimization**: Efficient database queries with proper indexes

## Business Rules (Section 5.5)

- **Free Tier**: 1 deep research report per month
- **Pro Tier**: 10 deep research reports per month
- **Premium Tier**: Unlimited deep research reports

## Monitoring & Logging

Logs are structured in JSON format for easy parsing:

```json
{
  "timestamp": "2025-12-17T10:30:00Z",
  "level": "INFO",
  "message": "Request processed",
  "request_id": "abc123",
  "duration": 0.123
}
```

## Troubleshooting

### Common Issues

1. **Database connection fails**
   - Check `SUPABASE_URL` and `DATABASE_URL` in `.env`
   - Verify Supabase project is active
   - Check network connectivity

2. **API key errors**
   - Verify all API keys are correctly set in `.env`
   - Check API key validity and quotas

3. **Vector search not working**
   - Ensure pgvector extension is enabled in Supabase
   - Run vector index creation SQL

## Support

For issues or questions:
- Create an issue in the repository
- Review the requirements document: `/documents/Requirements.docx`
- Check database schema: `/database/supabase_schema.sql`

## License

[Your License Here]

## Contributors

- Hai Phan (Lead Developer)

---

Built with ❤️ for value investors
