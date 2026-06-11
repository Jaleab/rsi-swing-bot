import ccxt
import pandas as pd
import os
from datetime import datetime, timedelta

def fetch_sol_usdt_candles(exchange_id='bybit', timeframe='1h', limit=1000, since_days=365):
    """
    Fetches SOL/USDT historical OHLCV data from a specified exchange.

    Args:
        exchange_id (str): The exchange ID (e.g., 'bybit', 'binance').
        timeframe (str): The timeframe for the candles (e.g., '1h', '4h', '1d').
        limit (int): The maximum number of candles to fetch per request.
        since_days (int): Number of days back from which to fetch data.

    Returns:
        pd.DataFrame: DataFrame containing OHLCV data with datetime index.
    """
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({
        'enableRateLimit': True,
    })

    symbol = 'SOL/USDT'
    all_candles = []

    # Calculate the start timestamp in milliseconds
    since_ms = exchange.parse8601((datetime.now() - timedelta(days=since_days)).isoformat())

    print(f"Fetching {symbol} {timeframe} candles from {exchange_id} starting from {datetime.fromtimestamp(since_ms / 1000)}")

    while True:
        try:
            # Fetch candles
            candles = exchange.fetch_ohlcv(symbol, timeframe, since_ms, limit)
            if not candles:
                break

            all_candles.extend(candles)
            
            # Update since_ms to the timestamp of the last fetched candle + 1 to avoid duplicates
            since_ms = candles[-1][0] + 1
            print(f"Fetched {len(candles)} candles. Total: {len(all_candles)}. Latest timestamp: {datetime.fromtimestamp(candles[-1][0] / 1000)}")

            # Break if we have enough data or no more data is available
            if len(candles) < limit:
                break
            
        except ccxt.NetworkError as e:
            print(f"Network error: {e}. Retrying...")
            continue
        except ccxt.ExchangeError as e:
            print(f"Exchange error: {e}. Exiting.")
            break
        except Exception as e:
            print(f"An unexpected error occurred: {e}. Exiting.")
            break

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True) # Ensure chronological order

    print(f"Successfully fetched {len(df)} candles.")
    return df

if __name__ == "__main__":
    # Example usage:
    # Fetch 1 year of 1-hour candles from Bybit
    df_1h = fetch_sol_usdt_candles(exchange_id='bybit', timeframe='1h', since_days=365)
    if not df_1h.empty:
        print("\nFirst 5 rows of 1h data:")
        print(df_1h.head())
        print("\nLast 5 rows of 1h data:")
        print(df_1h.tail())
        df_1h.to_csv('SOLUSDT_1h.csv')
        print("1h data saved to SOLUSDT_1h.csv")

    # Fetch 1 year of 4-hour candles from Bybit
    df_4h = fetch_sol_usdt_candles(exchange_id='bybit', timeframe='4h', since_days=365)
    if not df_4h.empty:
        print("\nFirst 5 rows of 4h data:")
        print(df_4h.head())
        print("\nLast 5 rows of 4h data:")
        print(df_4h.tail())
        df_4h.to_csv('SOLUSDT_4h.csv')
        print("4h data saved to SOLUSDT_4h.csv")