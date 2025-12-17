# Testing Guide for AI Value Investor Backend

This guide covers how to test all Phase 2 components.

## 📋 Prerequisites

### 1. Install Dependencies

```bash
cd backend

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

**If you encounter dependency conflicts**, install core packages first:
```bash
pip install fastapi uvicorn supabase redis apscheduler sqlalchemy pydantic-settings
```

### 2. Environment Setup

Create `.env` file in `backend/` directory:

```bash
# Copy example
cp .env.example .env

# Edit with your credentials
nano .env  # or use your preferred editor
```

**Required variables**:
```env
# Security
SECRET_KEY=your-secret-key-here

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-role-key

# Gemini AI
GEMINI_API_KEY=your-gemini-api-key

# Financial Modeling Prep
FMP_API_KEY=your-fmp-api-key

# Redis (optional for testing without cache)
REDIS_URL=redis://localhost:6379/0

# Background Jobs (disable for local testing)
ENABLE_BACKGROUND_JOBS=False
```

---

## 🚀 Test 1: Start the Server

### Basic Server Test

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Expected output**:
```
INFO:     Starting AI Value Investor API
INFO:     Environment: development
INFO:     Debug mode: True
INFO:     ✓ Database connection established
INFO:     ✓ Redis cache connected
INFO:     Background jobs disabled (ENABLE_BACKGROUND_JOBS=False)
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Test Health Endpoint

Open another terminal:
```bash
curl http://localhost:8000/api/v1/health
```

**Expected response**:
```json
{
  "status": "healthy",
  "database": "connected",
  "cache": "connected",
  "version": "1.0.0"
}
```

---

## 📖 Test 2: Interactive API Docs

FastAPI provides automatic interactive documentation.

### Swagger UI

1. Start the server
2. Open browser: http://localhost:8000/api/docs
3. You'll see all endpoints with "Try it out" buttons

### Test Endpoints Directly

**Example: User Stats**
1. Go to `/api/v1/users/me/stats`
2. Click "Try it out"
3. Add authentication token (if required)
4. Click "Execute"

---

## 🔐 Test 3: Authentication Flow

### Register New User

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "Test1234!",
    "full_name": "Test User"
  }'
```

### Login

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "Test1234!"
  }'
```

**Save the access_token** from response for subsequent requests.

---

## 💰 Test 4: User Service (Credit Management)

### Check User Credits

```bash
# Replace YOUR_TOKEN with actual token from login
curl http://localhost:8000/api/v1/users/me/usage \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Expected response**:
```json
{
  "tier": "free",
  "has_credits": true,
  "deep_research": {
    "used": 0,
    "limit": 1,
    "remaining": 1,
    "reset_at": null
  }
}
```

### Get User Statistics

```bash
curl http://localhost:8000/api/v1/users/me/stats \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Expected response**:
```json
{
  "user_id": "...",
  "email": "test@example.com",
  "tier": "free",
  "is_premium": false,
  "credits_remaining": 1,
  "credits_used": 0,
  "credits_total": 1,
  "total_reports_generated": 0,
  "total_chat_messages": 0
}
```

---

## 📊 Test 5: Widget Service

### Generate Widget Data

Create a Python test script:

```python
# test_widget.py
import asyncio
from app.database import get_supabase
from app.services.widget_service import WidgetService

async def test_widget():
    supabase = get_supabase()
    widget_service = WidgetService(supabase)

    # Replace with real user_id
    user_id = "your-user-id"

    # Generate widget
    widget = await widget_service.generate_widget_update(user_id)

    print("Widget Update:")
    print(f"  Headline: {widget.headline}")
    print(f"  Sentiment: {widget.sentiment}")
    print(f"  Emoji: {widget.emoji}")
    print(f"  Daily Trend: {widget.daily_trend}")

if __name__ == "__main__":
    asyncio.run(test_widget())
```

Run:
```bash
python test_widget.py
```

---

## 📰 Test 6: Deep Research with Credit Check

### Generate Research Report (Uses Credits)

```bash
curl -X POST http://localhost:8000/api/v1/research/generate \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "stock_id": "stock-uuid-here",
    "investor_persona": "buffett",
    "analysis_period": "annual"
  }'
```

**What happens**:
1. ✅ Credits checked BEFORE Gemini call
2. ✅ Report created in "pending" status
3. ✅ Background task started
4. ✅ Credits decremented ONLY on success

### Verify Credit Was Decremented

```bash
curl http://localhost:8000/api/v1/users/me/usage \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Should show `"used": 1` after successful generation.

---

## ⏰ Test 7: Background Jobs (Scheduler)

### Enable Background Jobs

Edit `.env`:
```env
ENABLE_BACKGROUND_JOBS=True
```

### Check Scheduler Status

Create test script:

```python
# test_scheduler.py
from app.jobs.scheduler import get_scheduler_status

status = get_scheduler_status()

print("Scheduler Status:")
print(f"  Running: {status['running']}")
print(f"  Timezone: {status['timezone']}")
print(f"  Jobs: {status['jobs_count']}")
print("\nScheduled Jobs:")
for job in status['jobs']:
    print(f"  - {job['name']}")
    print(f"    Next run: {job['next_run_time']}")
```

Run:
```bash
python test_scheduler.py
```

**Expected output**:
```
Scheduler Status:
  Running: True
  Timezone: America/New_York
  Jobs: 13

Scheduled Jobs:
  - Fetch News for Watchlist Stocks
    Next run: 2025-12-17T09:30:00
  - Update Widgets for Active Users
    Next run: 2025-12-17T07:15:00
  ...
```

### Test Individual Jobs

```python
# test_jobs.py
import asyncio
from app.jobs.news_jobs import fetch_market_news

async def test():
    print("Testing market news fetch...")
    await fetch_market_news()
    print("✓ Job completed")

asyncio.run(test())
```

---

## 🗄️ Test 8: Redis Cache

### Test Cache Connection

```python
# test_cache.py
import asyncio
from app.cache import cache_manager

async def test_cache():
    # Connect
    await cache_manager.connect()

    if cache_manager.is_connected:
        print("✓ Redis connected")

        # Test set
        await cache_manager.set("test_key", {"data": "test"}, ttl=60)
        print("✓ Set cached value")

        # Test get
        value = await cache_manager.get("test_key")
        print(f"✓ Retrieved: {value}")

        # Test delete
        await cache_manager.delete("test_key")
        print("✓ Deleted cached value")

        await cache_manager.disconnect()
    else:
        print("✗ Redis not connected (this is OK for testing)")

asyncio.run(test_cache())
```

---

## 🧪 Test 9: Unit Tests with Pytest

### Create Test File

```python
# tests/test_user_service.py
import pytest
from app.services.user_service import UserService

@pytest.mark.asyncio
async def test_check_user_credits(mock_supabase):
    """Test credit checking for different tiers"""
    service = UserService(mock_supabase)

    # Test FREE tier
    has_credits = await service.check_user_credits("free-user-id")
    assert has_credits == True

@pytest.mark.asyncio
async def test_decrement_credits(mock_supabase):
    """Test credit decrement"""
    service = UserService(mock_supabase)

    success = await service.decrement_credits("user-id", credits=1)
    assert success == True
```

### Run Tests

```bash
# Install pytest if needed
pip install pytest pytest-asyncio

# Run all tests
pytest tests/ -v

# Run specific test
pytest tests/test_user_service.py -v

# Run with coverage
pytest tests/ --cov=app --cov-report=html
```

---

## 🔍 Test 10: Manual Integration Test

### Complete Flow Test

```python
# integration_test.py
import asyncio
from app.database import get_supabase
from app.services.user_service import UserService
from app.services.widget_service import WidgetService

async def full_integration_test():
    supabase = get_supabase()

    print("=== Integration Test ===\n")

    # 1. User Service
    print("1. Testing User Service...")
    user_service = UserService(supabase)
    user_id = "test-user-id"

    has_credits = await user_service.check_user_credits(user_id)
    print(f"   ✓ Has credits: {has_credits}")

    stats = await user_service.get_user_stats(user_id)
    if stats:
        print(f"   ✓ User stats retrieved: {stats['tier']}")

    # 2. Widget Service
    print("\n2. Testing Widget Service...")
    widget_service = WidgetService(supabase)

    widget = await widget_service.generate_widget_update(user_id)
    print(f"   ✓ Widget generated: {widget.headline}")

    # 3. Cache
    print("\n3. Testing Cache...")
    from app.cache import cache_manager
    await cache_manager.connect()

    if cache_manager.is_connected:
        await cache_manager.set("test", "value", ttl=10)
        value = await cache_manager.get("test")
        print(f"   ✓ Cache working: {value}")
    else:
        print("   ⚠ Cache not available (OK)")

    print("\n=== All Tests Passed! ===")

if __name__ == "__main__":
    asyncio.run(full_integration_test())
```

Run:
```bash
python integration_test.py
```

---

## 📝 Common Issues & Solutions

### Issue: "Module not found"
**Solution**: Install dependencies
```bash
pip install -r requirements.txt
```

### Issue: "Database connection failed"
**Solution**: Check Supabase credentials in `.env`

### Issue: "Redis connection failed"
**Solution**:
- Install Redis: `brew install redis` (Mac) or `apt install redis` (Linux)
- Start Redis: `redis-server`
- Or set `REDIS_URL=redis://localhost:6379/0` in `.env`

### Issue: "Background jobs not starting"
**Solution**: Set `ENABLE_BACKGROUND_JOBS=True` in `.env`

### Issue: "Import errors"
**Solution**: Make sure you're in the right directory
```bash
cd backend
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

---

## ✅ Quick Test Checklist

- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] `.env` file configured
- [ ] Server starts without errors
- [ ] Health endpoint responds
- [ ] Swagger UI loads at `/api/docs`
- [ ] Database connection successful
- [ ] Redis cache connected (optional)
- [ ] Background jobs initialized (optional)
- [ ] User registration works
- [ ] User login returns token
- [ ] Credit checking works
- [ ] Widget generation works
- [ ] Research report creation works

---

## 🎯 Recommended Testing Order

1. **Start simple**: Health check → Swagger UI
2. **Test auth**: Register → Login → Get token
3. **Test services**: User stats → Widget generation
4. **Test credits**: Check → Generate report → Verify decrement
5. **Test background**: Enable jobs → Check scheduler
6. **Test cache**: Connect → Set → Get → Delete

---

## 📞 Need Help?

If something doesn't work:
1. Check the logs (they're very detailed)
2. Verify `.env` variables
3. Ensure database is accessible
4. Try with background jobs disabled first
5. Check if Redis is running (optional)

Good luck testing! 🚀
