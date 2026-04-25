### Take ~5 hours 



import pandas as pd
import yfinance as yf
import os
import time
import random
from datetime import datetime
import json
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
# ===================================
# RELAXED QUALITY FILTERS (for initial testing)
# ===================================

# # Loosened from strict version
# MIN_HISTORY_YEARS = 3          # Reduced from 5
# MIN_PRICE = 5.0                # Keep as is
# MIN_AVG_VOLUME = 500_000       # Reduced from 1M
# MIN_TRADING_DAYS = 750         # Reduced from 1260 (~3 years)
# MAX_PRICE = 100000             # Keep as is
# MIN_MARKET_CAP = 1_000_000_000

# # Advanced quality filters (loosened)
# MAX_MISSING_DATA_PCT = 0.05    # Increased from 0.02 (allow 5%)
# MAX_ZERO_VOLUME_PCT = 0.10     # Increased from 0.05 (allow 10%)
# MIN_PRICE_VARIANCE = 0.0003    # Reduced from 0.001 - allows stable mega-caps
# MAX_VOLATILITY = 0.35          # Increased from 0.30 (very loose)

# RATE_LIMIT_DELAY = 1
# PERIOD = 'max'
# MAX_WORKERS = 5

MIN_HISTORY_YEARS = 5          # Reduced from 5
MIN_PRICE = 5.0                # Keep as is
MIN_AVG_VOLUME = 1_000_000       # Reduced from 1M
MIN_TRADING_DAYS = 1260         # Reduced from 1260 (~3 years)
MAX_PRICE = 100000             # Keep as is
MIN_MARKET_CAP = 1_000_000_000 # $1 billion minimum

# Advanced quality filters (user-specified)
MAX_MISSING_DATA_PCT = 0.05    # Increased from 0.02 (allow 5%)
MAX_ZERO_VOLUME_PCT = 0.10     # Increased from 0.05 (allow 10%)
MIN_PRICE_VARIANCE = 0.0003    # Reduced from 0.001 - allows stable mega-caps
MAX_VOLATILITY = 0.35          # 35% max volatility


PERIOD = 'max'
MAX_WORKERS = 3  # REDUCED from 10 to avoid rate limiting
RATE_LIMIT_DELAY = 2.0  # Base delay between requests
MAX_RETRIES = 3  # NEW: Retry failed downloads
BACKOFF_MULTIPLIER = 2  # NEW: Exponential backoff




OUTPUT_DIR = "stocks_filtered"
FUNDAMENTALS_DIR = "fundamentals"
METADATA_FILE = "download_metadata.json"
ERROR_LOG_FILE = "download_errors.log"

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(FUNDAMENTALS_DIR, exist_ok=True)

download_lock = Lock()
last_download_time = time.time()

# ===================================
# GET NASDAQ SYMBOLS WITH PRE-FILTERING
# ===================================

print("="*60)
print("ENHANCED NASDAQ STOCK DATA DOWNLOADER")
print("="*60)
print("\nFetching NASDAQ symbols...")

data = pd.read_csv("http://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt", sep='|')
data_clean = data[data['Test Issue'] == 'N']

data_clean = data_clean[data_clean['ETF'] == 'N']

symbols = [s for s in data_clean['NASDAQ Symbol'].tolist()
           if isinstance(s, str) and not any(c in s for c in ['$', '^', '.', '/', ' '])]

symbols = [s for s in symbols if not any(s.endswith(suffix) for suffix in
           ['W', 'WS', 'U', 'UN', 'R', 'P', 'Q'])]

print(f'Symbols after pre-filtering: {len(symbols)}')

def to_scalar(value):
    """
    Safely convert pandas Series or scalar to Python scalar.
    Handles FutureWarning about calling int/float on Series.
    """
    if hasattr(value, 'item'):
        return value.item()
    elif hasattr(value, '__len__') and len(value) == 1:
        return value.iloc[0] if hasattr(value, 'iloc') else value[0]
    else:
        return value

# ===================================
# FIXED VALIDATION FUNCTION
# ===================================

def validate_data_enhanced(df, symbol):
    """
    FIXED: Properly handles Series to avoid ambiguity errors
    Returns (is_valid, reason, quality_score)
    """
    try:
        # Check for empty dataframe
        if df is None or df.empty or len(df) == 0:
            return False, "Empty dataframe", 0

        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        if not all(col in df.columns for col in required_cols):
            return False, "Missing required columns", 0

        quality_score = 100

        # 1. Trading days check
        if len(df) < MIN_TRADING_DAYS:
            return False, f"Only {len(df)} trading days", 0

        # 2. Date range check
        try:
            date_range = (df.index[-1] - df.index[0]).days / 365.25
            if date_range < MIN_HISTORY_YEARS:
                return False, f"Only {date_range:.1f} years", 0
        except:
            return False, "Invalid date range", 0

        # 3. Price validation - Using helper function
        close_series = df['Close']

        try:
            close_min = to_scalar(close_series.min())
            close_max = to_scalar(close_series.max())
            close_mean = to_scalar(close_series.mean())
        except:
            return False, "Invalid price data", 0

        # Now we can safely use scalar comparisons
        if pd.isna(close_min) or pd.isna(close_max) or pd.isna(close_mean):
            return False, "NaN prices detected", 0

        if close_min < 0:
            return False, "Negative prices detected", 0

        if close_max > MAX_PRICE:
            return False, f"Suspicious max price: ${close_max:,.2f}", 0

        if close_mean < MIN_PRICE:
            return False, f"Average price ${close_mean:.2f} below minimum", 0

        quality_score -= 5 if close_mean < 10 else 0

        # 4. Volume validation - Using helper function
        try:
            avg_volume = to_scalar(df['Volume'].mean())
        except:
            return False, "Invalid volume data", 0

        if pd.isna(avg_volume) or avg_volume < MIN_AVG_VOLUME:
            return False, f"Average volume {avg_volume:,.0f} below minimum", 0

        quality_score += 5 if avg_volume > 5_000_000 else 0

        # 5. Missing data check
        missing_pct = df[required_cols].isnull().sum().sum() / (len(df) * len(required_cols))
        if missing_pct > MAX_MISSING_DATA_PCT:
            return False, f"Too much missing data: {missing_pct:.1%}", 0
        quality_score -= int(missing_pct * 1000)

        # 6. Zero volume check - Using helper function
        zero_volume_count = to_scalar((df['Volume'] == 0).sum())
        zero_volume_pct = zero_volume_count / len(df)

        if zero_volume_pct > MAX_ZERO_VOLUME_PCT:
            return False, f"Too many zero-volume days: {zero_volume_pct:.1%}", 0
        quality_score -= int(zero_volume_pct * 200)

        # 7. Price movement check - Using helper function
        daily_returns = df['Close'].pct_change().dropna()

        if len(daily_returns) == 0:
            return False, "Cannot calculate returns", 0

        try:
            price_variance = to_scalar(daily_returns.var())
        except:
            return False, "Invalid variance calculation", 0

        if pd.isna(price_variance) or price_variance < MIN_PRICE_VARIANCE:
            return False, "Insufficient price movement", 0

        # 8. Volatility check - Using helper function
        try:
            daily_volatility = to_scalar(daily_returns.std())
        except:
            return False, "Invalid volatility calculation", 0

        if pd.isna(daily_volatility) or daily_volatility > MAX_VOLATILITY:
            return False, f"Excessive volatility: {daily_volatility:.1%}", 0

        quality_score -= int(max(0, (daily_volatility - 0.05) * 100))

        # 9. Check for data gaps
        date_diffs = df.index.to_series().diff()
        max_gap = int(date_diffs.max().days) if len(date_diffs) > 0 else 0

        if max_gap > 30:
            quality_score -= 20

        # 10. Check for suspicious patterns - FIX: Use .any() properly
        price_drops = df['Close'].pct_change()
        has_suspicious_drops = bool((price_drops < -0.5).any())  # 🔥 Changed from -0.4 to -0.5

        if has_suspicious_drops:
            return False, "Detected potential unadjusted stock split", 0

        # 11. Recent activity check
        recent_data = df.iloc[-252:] if len(df) >= 252 else df

        if len(recent_data) < 200:
            return False, "Insufficient recent trading history", 0

        try:
            recent_avg_volume = to_scalar(recent_data['Volume'].mean())
        except:
            recent_avg_volume = avg_volume

        if recent_avg_volume < MIN_AVG_VOLUME * 0.5:
            return False, "Declining liquidity", 0

        # Clamp quality score
        quality_score = max(0, min(100, quality_score))

        return True, "Valid", quality_score

    except Exception as e:
        return False, f"Validation error: {str(e)}", 0

# ===================================
# RATE-LIMITED DOWNLOAD FUNCTION
# ===================================

def download_with_rate_limit(symbol, ticker): # <-- Note: Pass ticker in
    """
    🔥 MODIFIED: Accepts a Ticker object
    """
    global last_download_time

    for attempt in range(MAX_RETRIES):
        try:
            # --- Thread-safe rate limiting ---
            with download_lock:
                current_time = time.time()
                time_since_last = current_time - last_download_time

                if time_since_last < RATE_LIMIT_DELAY:
                    sleep_time = (RATE_LIMIT_DELAY - time_since_last) + random.uniform(0.1, 0.5)
                    time.sleep(sleep_time)

                last_download_time = time.time()
            # --- End of locked section ---

            # 🔥 MODIFIED: Use the passed ticker object
            data = ticker.history(
                period=PERIOD,
                auto_adjust=True
            )

            # 🔥 FIX: Safe timezone handling with hasattr check
            if hasattr(data.index, 'tz') and data.index.tz is not None:
                data.index = data.index.tz_localize(None)

            # 🔥 FIX: Safe MultiIndex handling
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            return data, None  # Success!

        except Exception as e:
            error_msg = str(e)

            # Check if it's a rate limit error
            if any(phrase in error_msg.lower() for phrase in ['rate limit', 'too many requests', '429']):
                if attempt < MAX_RETRIES - 1:
                    # Exponential backoff: 2s, 4s, 8s
                    wait_time = RATE_LIMIT_DELAY * (BACKOFF_MULTIPLIER ** attempt)
                    print(f"      ⚠️  {symbol}: Rate limited. Waiting {wait_time:.1f}s (attempt {attempt+2}/{MAX_RETRIES})")
                    time.sleep(wait_time)
                    continue
                else:
                    return None, "Rate limit exceeded after retries"
            else:
                # Other errors - don't retry
                return None, error_msg

    return None, "Max retries exceeded"

# ===================================
# PARALLEL DOWNLOAD FUNCTION
# ===================================
def get_info_with_rate_limit(ticker):
    """
    🔥 NEW: Safely get ticker.info with rate limiting and retries.
    """
    global last_download_time

    for attempt in range(MAX_RETRIES):
        try:
            # --- Thread-safe rate limiting ---
            with download_lock:
                current_time = time.time()
                time_since_last = current_time - last_download_time

                if time_since_last < RATE_LIMIT_DELAY:
                    sleep_time = (RATE_LIMIT_DELAY - time_since_last) + random.uniform(0.1, 0.5)
                    time.sleep(sleep_time)

                last_download_time = time.time()
            # --- End of locked section ---

            # This is the actual API call
            return ticker.info, None

        except Exception as e:
            error_msg = str(e)
            if "404 Client Error" in error_msg or "No data found" in error_msg:
                 return None, "No info found (404)" # Don't retry for 404s

            if any(phrase in error_msg.lower() for phrase in ['rate limit', 'too many requests', '429']):
                if attempt < MAX_RETRIES - 1:
                    wait_time = RATE_LIMIT_DELAY * (BACKOFF_MULTIPLIER ** attempt)
                    print(f"      ⚠️  {ticker.ticker}: Info rate limited. Waiting {wait_time:.1f}s")
                    time.sleep(wait_time)
                    continue
                else:
                    return None, "Rate limit exceeded (info)"
            else:
                # Other errors
                return None, f"Info error: {error_msg}"

    return None, "Max retries exceeded (info)"



# ===================================
# PARALLEL DOWNLOAD FUNCTION
# ===================================

def download_single_stock(symbol):
    """
    🔥 FIXED: Worker function with market cap pre-filter
    """
    try:
        # Create Ticker object once
        ticker = yf.Ticker(symbol)

        # --- 1. PRE-FILTER: Market Cap ---
        info, error = get_info_with_rate_limit(ticker)

        if info is None:
            return {'symbol': symbol, 'status': 'error', 'reason': error, 'quality_score': 0}

        market_cap = info.get('marketCap')
        if not market_cap or market_cap < MIN_MARKET_CAP:
            return {
                'symbol': symbol,
                'status': 'invalid',
                'reason': f"Market cap {market_cap} below minimum",
                'quality_score': 0
            }

        # --- 2. DOWNLOAD PRICE HISTORY ---
        # 🔥 MODIFIED: Pass the ticker object to save an API call
        data, error = download_with_rate_limit(symbol, ticker)

        if data is None:
            return {
                'symbol': symbol,
                'status': 'error',
                'reason': error,
                'quality_score': 0
            }

        # --- 3. VALIDATE DATA QUALITY ---
        is_valid, reason, quality_score = validate_data_enhanced(data, symbol)

        if is_valid:
            filepath = os.path.join(OUTPUT_DIR, f"{symbol}.csv")
            data.to_csv(filepath)

            avg_volume = to_scalar(data['Volume'].mean())
            avg_price = to_scalar(data['Close'].mean())

            return {
                'symbol': symbol,
                'status': 'success',
                'quality_score': quality_score,
                'days': len(data),
                'avg_volume': int(avg_volume),
                'avg_price': float(avg_price),
                'market_cap': int(market_cap) # <-- Store this!
            }
        else:
            return {
                'symbol': symbol,
                'status': 'invalid',
                'reason': reason,
                'quality_score': quality_score
            }

    except Exception as e:
        return {
            'symbol': symbol,
            'status': 'error',
            'reason': f"Unexpected error: {str(e)}",
            'quality_score': 0
        }
# ===================================
# MAIN DOWNLOAD LOGIC
# ===================================

metadata = {
    'download_date': datetime.now().isoformat(),
    'total_symbols': len(symbols),
    'valid_symbols': [],
    'invalid_symbols': {},
    'config': {
        'min_history_years': MIN_HISTORY_YEARS,
        'min_price': MIN_PRICE,
        'min_avg_volume': MIN_AVG_VOLUME,
        'min_trading_days': MIN_TRADING_DAYS,
        'min_market_cap': MIN_MARKET_CAP,
        'period': PERIOD,
        'max_workers': MAX_WORKERS,
        'rate_limit_delay': RATE_LIMIT_DELAY
    }
}

print("\n" + "="*60)
print("STARTING PARALLEL DOWNLOAD")
print(f"Using {MAX_WORKERS} parallel workers (rate-limited)")
print("="*60)
print("\nFILTER SETTINGS:")
print(f"  - Min history: {MIN_HISTORY_YEARS} years ({MIN_TRADING_DAYS} days)")
print(f"  - Min price: ${MIN_PRICE}")
print(f"  - Min avg volume: {MIN_AVG_VOLUME:,}")
print(f"  - Min market cap: ${MIN_MARKET_CAP:,}")
print(f"  - Min price variance: {MIN_PRICE_VARIANCE}")
print(f"  - Max volatility: {MAX_VOLATILITY*100:.0f}%")
print(f"  - Rate limit delay: {RATE_LIMIT_DELAY}s per request")
print("="*60)

error_log = []
rate_limit_errors = 0
start_time = time.time()
completed = 0

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    # Submit all download tasks
    future_to_symbol = {
        executor.submit(download_single_stock, symbol): symbol
        for symbol in symbols
    }

    # Process results as they complete
    for future in as_completed(future_to_symbol):
        result = future.result()
        completed += 1

        if result['status'] == 'success':
            metadata['valid_symbols'].append({
                'symbol': result['symbol'],
                'quality_score': result['quality_score'],
                'days': result['days'],
                'avg_volume': result['avg_volume'],
                'avg_price': result['avg_price'],
                'market_cap': result['market_cap']
            })
            status = "✓"
        else:
            reason = result.get('reason', 'Unknown error')
            metadata['invalid_symbols'][result['symbol']] = reason
            error_log.append(f"{result['symbol']}: {reason}")
            status = "✗"

            # Track rate limit errors
            if 'rate limit' in reason.lower():
                rate_limit_errors += 1

        # Progress indicator
        if completed % 50 == 0 or completed == len(symbols):
            valid_count = len(metadata['valid_symbols'])
            invalid_count = len(metadata['invalid_symbols'])
            elapsed = time.time() - start_time
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (len(symbols) - completed) / rate / 60 if rate > 0 else 0

            print(f"[{completed:4d}/{len(symbols)}] {result['symbol']:6s} {status}  "
                  f"Valid: {valid_count:4d}  Invalid: {invalid_count:4d}  "
                  f"RateLimit: {rate_limit_errors:4d}  ETA: {eta:.1f}min")

# ===================================
# SAVE METADATA AND STATISTICS
# ===================================

print("\n" + "="*60)
print("DOWNLOAD COMPLETE")
print("="*60)

total_time = time.time() - start_time
success_rate = len(metadata['valid_symbols']) / len(symbols) * 100 if len(symbols) > 0 else 0

print(f"Valid symbols:   {len(metadata['valid_symbols']):,}")
print(f"Invalid symbols: {len(metadata['invalid_symbols']):,}")
print(f"Rate limit errors: {rate_limit_errors} ({rate_limit_errors/len(symbols)*100:.1f}%)")
print(f"Success rate:    {success_rate:.1f}%")
print(f"Total time:      {total_time/60:.1f} minutes ({total_time/3600:.1f} hours)")

# Sort valid symbols by quality score
metadata['valid_symbols'].sort(key=lambda x: x['quality_score'], reverse=True)

# Save metadata with quality scores
with open(METADATA_FILE, 'w') as f:
    json.dump(metadata, f, indent=2)
print(f"\nMetadata saved to: {METADATA_FILE}")

# Save error log
with open(ERROR_LOG_FILE, 'w') as f:
    f.write('\n'.join(error_log))
print(f"Error log saved to: {ERROR_LOG_FILE}")

# Show top rejection reasons
print("\n" + "="*60)
print("TOP REJECTION REASONS")
print("="*60)

reasons = {}
for symbol, reason in metadata['invalid_symbols'].items():
    key = reason.split('(')[0].strip()
    reasons[key] = reasons.get(key, 0) + 1

for reason, count in sorted(reasons.items(), key=lambda x: -x[1])[:15]:
    print(f"  {count:4d}  {reason}")

# Show quality distribution
print("\n" + "="*60)
print("QUALITY SCORE DISTRIBUTION")
print("="*60)

if metadata['valid_symbols']:
    quality_scores = [s['quality_score'] for s in metadata['valid_symbols']]
    print(f"Mean quality score: {np.mean(quality_scores):.1f}")
    print(f"Median quality score: {np.median(quality_scores):.1f}")
    print(f"Top 10 highest quality stocks:")
    for i, stock in enumerate(metadata['valid_symbols'][:10], 1):
        print(f"  {i:2d}. {stock['symbol']:6s} - Score: {stock['quality_score']}, "
              f"Avg Volume: {stock['avg_volume']:,}, Avg Price: ${stock['avg_price']:.2f}")
else:
    print("No valid symbols found.")

# Export high-quality subset
high_quality_symbols = [s['symbol'] for s in metadata['valid_symbols']
                        if s['quality_score'] >= 80]
print(f"\n{len(high_quality_symbols)} stocks with quality score >= 80")

if high_quality_symbols:
    with open('high_quality_symbols.txt', 'w') as f:
        f.write('\n'.join(high_quality_symbols))
    print("High-quality symbols saved to: high_quality_symbols.txt")

# ===================================
# VERIFY DOWNLOADED FILES
# ===================================

print("\n" + "="*60)
print("VERIFYING DOWNLOADED FILES")
print("="*60)

corrupted_files = []
for stock in metadata['valid_symbols']:
    symbol = stock['symbol']
    filepath = os.path.join(OUTPUT_DIR, f"{symbol}.csv")
    try:
        df = pd.read_csv(filepath, index_col=0, parse_dates=True)
        if df.empty:
            corrupted_files.append(f"{symbol}: Empty file")
        elif len(df) < 100:
            corrupted_files.append(f"{symbol}: Too short ({len(df)} rows)")
    except Exception as e:
        corrupted_files.append(f"{symbol}: Read error - {e}")

if corrupted_files:
    print(f"⚠️  Found {len(corrupted_files)} corrupted files:")
    for err in corrupted_files[:10]:
        print(f"  - {err}")
else:
    print("✓ All files verified successfully!")

print("\n" + "="*60)
print("DOWNLOAD SUMMARY")
print("="*60)
print(f"✓ Downloaded {len(metadata['valid_symbols'])} valid stocks")
print(f"✓ Quality scores range: {min(quality_scores) if quality_scores else 0}-{max(quality_scores) if quality_scores else 0}")
print(f"✓ Average quality score: {np.mean(quality_scores):.1f}" if quality_scores else "N/A")
print(f"✓ Rate limit errors: {rate_limit_errors} ({rate_limit_errors/len(symbols)*100:.1f}%)")
print("="*60)

## CHECK Yahoo Finance BLOCKED
# import yfinance as yf
# import time

# print("Testing if Yahoo Finance unblocked you...")
# time.sleep(2)  # Small delay first

# try:
#     ticker = yf.Ticker('AAPL')
#     df = ticker.history(period='5d')  # Just 5 days, very light request

#     if len(df) > 0:
#         print("✅ SUCCESS! You're unblocked!")
#         print(f"   Downloaded {len(df)} days of AAPL data")
#         print("   You can now run the main download script (slowly!)")
#     else:
#         print("⚠️  Got empty data, might still be blocked")
# except Exception as e:
#     if "Rate limit" in str(e) or "Too Many Requests" in str(e):
#         print("❌ Still blocked. Wait longer (try again in 2-4 hours)")
#     else:
#         print(f"❌ Different error: {e}")

# ## TEST DOWNLOAD BLUE CHIP STOCKS
# #!/usr/bin/env python
# # coding: utf-8

# import pandas as pd
# import yfinance as yf
# import numpy as np

# # Test with known good stocks
# TEST_SYMBOLS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX']

# print("="*70)
# print("DIAGNOSTIC TEST - Testing known good stocks")
# print("="*70)

# # Current filter settings (USER-SPECIFIED)
# MIN_HISTORY_YEARS = 3          # Reduced from 5
# MIN_PRICE = 5.0                # Keep as is
# MIN_AVG_VOLUME = 500_000       # Reduced from 1M
# MIN_TRADING_DAYS = 750         # Reduced from 1260 (~3 years)
# MAX_PRICE = 100000             # Keep as is
# MIN_MARKET_CAP = 1_000_000_000 # $1 billion minimum

# # Advanced quality filters (user-specified)
# MAX_MISSING_DATA_PCT = 0.05    # Increased from 0.02 (allow 5%)
# MAX_ZERO_VOLUME_PCT = 0.10     # Increased from 0.05 (allow 10%)
# MIN_PRICE_VARIANCE = 0.0003    # Reduced from 0.001 - allows stable mega-caps
# MAX_VOLATILITY = 0.35          # 35% max volatility

# RATE_LIMIT_DELAY = 1
# PERIOD = 'max'
# MAX_WORKERS = 5

# def to_scalar(value):
#     """Safely extract scalar"""
#     if hasattr(value, 'item'):
#         return value.item()
#     elif hasattr(value, '__len__') and len(value) == 1:
#         return value.iloc[0] if hasattr(value, 'iloc') else value[0]
#     else:
#         return value

# for symbol in TEST_SYMBOLS:
#     print(f"\n{'='*70}")
#     print(f"Testing: {symbol}")
#     print('='*70)

#     try:
#         # FIX: Use Ticker().history() to avoid wrong symbol download
#         ticker = yf.Ticker(symbol)
#         df = ticker.history(period='max', auto_adjust=True)

#         # Remove timezone and ensure simple columns
#         if df.index.tz is not None:
#             df.index = df.index.tz_localize(None)
#         if isinstance(df.columns, pd.MultiIndex):
#             df.columns = df.columns.get_level_values(0)

#         print(f"✓ Download successful")
#         print(f"  Shape: {df.shape}")
#         print(f"  Columns: {df.columns.tolist()}")
#         print(f"  Index type: {type(df.index)}")
#         print(f"  First date: {df.index[0]}")
#         print(f"  Last date: {df.index[-1]}")

#         # Check data structure
#         print(f"\n  Sample data (first 3 rows):")
#         print(df.head(3))

#         # Test each filter
#         print(f"\n  FILTER TESTS:")

#         # 1. Trading days
#         trading_days = len(df)
#         print(f"    Trading days: {trading_days} (need {MIN_TRADING_DAYS})")
#         print(f"      → {'✓ PASS' if trading_days >= MIN_TRADING_DAYS else '✗ FAIL'}")

#         # 2. Years of data
#         date_range = (df.index[-1] - df.index[0]).days / 365.25
#         print(f"    Years of data: {date_range:.1f} (need {MIN_HISTORY_YEARS})")
#         print(f"      → {'✓ PASS' if date_range >= MIN_HISTORY_YEARS else '✗ FAIL'}")

#         # 3. Average price
#         avg_price = to_scalar(df['Close'].mean())
#         print(f"    Average price: ${avg_price:.2f} (need ${MIN_PRICE})")
#         print(f"      → {'✓ PASS' if avg_price >= MIN_PRICE else '✗ FAIL'}")

#         # 4. Average volume
#         avg_volume = to_scalar(df['Volume'].mean())
#         print(f"    Average volume: {avg_volume:,.0f} (need {MIN_AVG_VOLUME:,})")
#         print(f"      → {'✓ PASS' if avg_volume >= MIN_AVG_VOLUME else '✗ FAIL'}")

#         # 5. Missing data
#         missing_pct = df.isnull().sum().sum() / (len(df) * len(df.columns))
#         print(f"    Missing data: {missing_pct:.2%}")
#         print(f"      → {'✓ PASS' if missing_pct <= 0.02 else '✗ FAIL'}")

#         # 6. Zero volume days
#         zero_volume_pct = to_scalar((df['Volume'] == 0).sum()) / len(df)
#         print(f"    Zero volume days: {zero_volume_pct:.2%}")
#         print(f"      → {'✓ PASS' if zero_volume_pct <= 0.05 else '✗ FAIL'}")

#         # 7. Price variance
#         daily_returns = df['Close'].pct_change().dropna()
#         price_variance = to_scalar(daily_returns.var())
#         print(f"    Price variance: {price_variance:.6f} (need {MIN_PRICE_VARIANCE})")
#         print(f"      → {'✓ PASS' if price_variance >= MIN_PRICE_VARIANCE else '✗ FAIL'}")

#         # 8. Volatility
#         daily_volatility = to_scalar(daily_returns.std())
#         print(f"    Daily volatility: {daily_volatility:.4f} ({daily_volatility*100:.2f}%)")
#         print(f"      → {'✓ PASS' if daily_volatility <= MAX_VOLATILITY else '✗ FAIL'} (max {MAX_VOLATILITY*100:.0f}%)")

#         # Overall verdict
#         passes_all = (
#             trading_days >= MIN_TRADING_DAYS and
#             date_range >= MIN_HISTORY_YEARS and
#             avg_price >= MIN_PRICE and
#             avg_volume >= MIN_AVG_VOLUME and
#             missing_pct <= MAX_MISSING_DATA_PCT and
#             zero_volume_pct <= MAX_ZERO_VOLUME_PCT and
#             price_variance >= MIN_PRICE_VARIANCE and
#             daily_volatility <= MAX_VOLATILITY
#         )

#         print(f"\n  OVERALL: {'✓✓✓ WOULD BE DOWNLOADED' if passes_all else '✗✗✗ WOULD BE REJECTED'}")

#     except Exception as e:
#         print(f"✗ Download failed: {e}")
#         import traceback
#         traceback.print_exc()

# print("\n" + "="*70)
# print("RECOMMENDATIONS:")
# print("="*70)
# print("✓ Data download is working correctly!")
# print("✓ You're getting the RIGHT stock data (not wrong symbols)")
# print("\nCurrent filter settings:")
# print(f"  - MIN_HISTORY_YEARS: {MIN_HISTORY_YEARS}")
# print(f"  - MIN_TRADING_DAYS: {MIN_TRADING_DAYS}")
# print(f"  - MIN_AVG_VOLUME: {MIN_AVG_VOLUME:,}")
# print(f"  - MIN_PRICE: ${MIN_PRICE}")
# print(f"  - MIN_MARKET_CAP: ${MIN_MARKET_CAP:,}")
# print(f"  - MIN_PRICE_VARIANCE: {MIN_PRICE_VARIANCE}")
# print(f"  - MAX_VOLATILITY: {MAX_VOLATILITY*100:.0f}%")
# print(f"  - MAX_MISSING_DATA_PCT: {MAX_MISSING_DATA_PCT*100:.0f}%")
# print(f"  - MAX_ZERO_VOLUME_PCT: {MAX_ZERO_VOLUME_PCT*100:.0f}%")
# print("\nThese filters should capture large/mega-cap quality stocks.")

# from google.colab import drive
# drive.mount('/content/drive')

# !cp -r /content/stocks_filtered/ /content/drive/MyDrive/

# !zip -r stocks_filtered.zip stocks_filtered

