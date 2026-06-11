import pandas as pd
import numpy as np
import itertools
import matplotlib.pyplot as plt
import ta
import multiprocessing
from functools import partial
from tqdm import tqdm
import os
import asyncio # New import for async backtest engine
import csv # For dummy liquidation events creation

from src.config import Config # Import the Config class
from src.backtest.backtest_engine import BacktestEngine # Import the new BacktestEngine

def calculate_sharpe_ratio(returns, risk_free_rate=0.0):
    """
    Calculate the Sharpe Ratio for a given series of returns.
    Assumes daily returns for annualization.
    """
    if len(returns) == 0 or returns.std() == 0:
        return 0.0
    
    annualized_return = returns.mean() * 252
    annualized_std = returns.std() * np.sqrt(252)
    
    return (annualized_return - risk_free_rate) / annualized_std

def calculate_max_drawdown(equity_curve):
    """
    Calculate the maximum drawdown from an equity curve.
    """
    if equity_curve.empty:
        return 0.0
    
    peak = equity_curve.expanding(min_periods=1).max()
    drawdown = (equity_curve - peak) / peak
    return drawdown.min()

def run_single_backtest_sync(params, df_ohlcv, historical_liquidation_file):
    """
    Synchronous wrapper for running an async backtest in a multiprocessing pool.
    """
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # If a loop is already running (e.g., in a notebook or interactive session),
        # create a new one for this thread/process.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(run_single_backtest_async(params, df_ohlcv, historical_liquidation_file))

async def run_single_backtest_async(params, df_ohlcv, historical_liquidation_file):
    """
    Runs a single backtest with the given parameters using the new BacktestEngine.
    """
    (rsi_len, oversold, overbought, sl_pct, tp_pct, 
     bin_pct, sweep_thresh_factor, min_sweep_vol) = params
    
    # Skip invalid parameter combinations
    if oversold >= overbought:
        return None # Return None for invalid combinations

    # Temporarily modify a Config instance for this backtest run
    # This assumes Config can be modified or an instance can be passed around
    # For a multiprocessing context, it's safer to create a new Config-like object or pass all params
    
    # Create a temporary Config object for this run
    temp_config = Config()
    temp_config.RSI_LENGTH = rsi_len
    temp_config.RSI_OVERSOLD = oversold
    temp_config.RSI_OVERBOUGHT = overbought
    temp_config.STOP_LOSS_PERCENT = sl_pct
    temp_config.TAKE_PROFIT_PERCENT = tp_pct
    temp_config.BIN_PCT = bin_pct
    temp_config.SWEEP_THRESHOLD_FACTOR = sweep_thresh_factor
    temp_config.MIN_SWEEP_VOLUME_USDT = min_sweep_vol
    temp_config.SIM_MODE = True # Backtests are always simulated
    temp_config.LIVE_MODE = False

    backtest_engine = BacktestEngine(temp_config, df_ohlcv.copy(), historical_liquidation_file)
    await backtest_engine.run_backtest()

    # Read the generated trade log for analysis
    trade_log_path = backtest_engine.trade_log_path
    if not os.path.exists(trade_log_path) or os.stat(trade_log_path).st_size == 0:
        print(f"No trade log generated for params: {params}")
        return {
            'rsi_length': rsi_len, 'oversold_level': oversold, 'overbought_level': overbought,
            'stop_loss_percent': sl_pct, 'take_profit_percent': tp_pct,
            'bin_pct': bin_pct, 'sweep_threshold_factor': sweep_thresh_factor, 'min_sweep_volume_usdt': min_sweep_vol,
            'total_trades': 0, 'net_profit_%': 0, 'win_rate_%': 0, 'sharpe_ratio': 0, 'max_drawdown_%': 0,
            'rsi_only_trades': 0, 'rsi_sweep_trades': 0, 'cluster_only_trades': 0
        }
    
    backtest_results_df = pd.read_csv(trade_log_path)
    
    total_trades = backtest_results_df[backtest_results_df['Trade Type'] == 'Exit'].shape[0]
    
    # For simplicity, calculate PnL based on sum of PNL column for exited trades
    # A more robust equity curve calculation would be needed for accurate Sharpe/Drawdown
    if total_trades > 0:
        net_profit = backtest_results_df['PNL'].sum() # Assuming PNL is in USDT
        # Convert net_profit to percentage (requires initial capital)
        # For now, let's just use raw PNL or assume a fixed initial capital for %
        initial_capital = 1000 # Example initial capital for % calculation
        net_profit_pct = (net_profit / initial_capital) * 100

        win_trades = backtest_results_df[(backtest_results_df['Trade Type'] == 'Exit') & (backtest_results_df['PNL'] > 0)].shape[0]
        win_rate = (win_trades / total_trades) * 100

        # Simplified Sharpe and Max Drawdown calculation - needs equity curve reconstruction
        # For now, placeholder values or simplified calculation
        sharpe_ratio = net_profit / (backtest_results_df['PNL'].std() if backtest_results_df['PNL'].std() > 0 else 1) # Very crude
        max_drawdown = 0 # Placeholder

        # Categorize trades by reason
        rsi_only_trades = backtest_results_df[backtest_results_df['Reason'] == 'RSI_ONLY'].shape[0]
        rsi_sweep_trades = backtest_results_df[backtest_results_df['Reason'] == 'RSI+SWEEP'].shape[0]
        cluster_only_trades = backtest_results_df[backtest_results_df['Reason'] == 'CLUSTER_ONLY'].shape[0] # To be implemented in SignalGenerator
        fallback_rsi_trades = backtest_results_df[backtest_results_df['Reason'] == 'FALLBACK_RSI'].shape[0]

        return {
            'rsi_length': rsi_len, 'oversold_level': oversold, 'overbought_level': overbought,
            'stop_loss_percent': sl_pct, 'take_profit_percent': tp_pct,
            'bin_pct': bin_pct, 'sweep_threshold_factor': sweep_thresh_factor, 'min_sweep_volume_usdt': min_sweep_vol,
            'total_trades': total_trades, 'net_profit_%': net_profit_pct, 'win_rate_%': win_rate,
            'sharpe_ratio': sharpe_ratio, 'max_drawdown_%': max_drawdown,
            'rsi_only_trades': rsi_only_trades, 'rsi_sweep_trades': rsi_sweep_trades, 
            'cluster_only_trades': cluster_only_trades, 'fallback_rsi_trades': fallback_rsi_trades
        }
    else:
        return {
            'rsi_length': rsi_len, 'oversold_level': oversold, 'overbought_level': overbought,
            'stop_loss_percent': sl_pct, 'take_profit_percent': tp_pct,
            'bin_pct': bin_pct, 'sweep_threshold_factor': sweep_thresh_factor, 'min_sweep_volume_usdt': min_sweep_vol,
            'total_trades': 0, 'net_profit_%': 0, 'win_rate_%': 0, 'sharpe_ratio': 0, 'max_drawdown_%': 0,
            'rsi_only_trades': 0, 'rsi_sweep_trades': 0, 'cluster_only_trades': 0, 'fallback_rsi_trades': 0
        }

if __name__ == "__main__":
    # --- Data Fetching ---
    # Load OHLCV data
    # For a real grid search, you'd fetch/load historical OHLCV data for relevant timeframes
    # For now, we'll use a single dummy OHLCV for demonstration
    ohlcv_data = [
        [1678886400000, 100.0, 101.0, 99.5, 100.5, 1000], 
        [1678886460000, 100.5, 100.8, 100.0, 100.2, 1200], 
        [1678886520000, 100.2, 100.7, 99.8, 100.1, 1100], # RSI oversold cross (example)
        [1678886580000, 100.1, 100.5, 100.0, 100.4, 1500], # Price crosses up (Buy signal)
        [1678886640000, 100.4, 101.8, 100.3, 101.5, 1300], 
        [1678886700000, 101.5, 102.0, 101.0, 101.8, 1400], 
        [1678886760000, 101.8, 102.5, 101.7, 102.3, 1100], 
        [1678886820000, 102.3, 102.5, 102.0, 102.1, 1000], # RSI overbought cross (example)
        [1678886880000, 102.1, 102.3, 101.5, 101.6, 1200], # Price crosses down (Sell signal)
        [1678886940000, 101.6, 101.8, 101.0, 101.2, 1300],
        [1678887000000, 101.2, 101.5, 100.8, 100.9, 1400],
        [1678887060000, 100.9, 101.1, 100.5, 100.7, 1500],
        [1678887120000, 100.7, 100.9, 100.0, 100.3, 1600],
        [1678887180000, 100.3, 100.6, 99.9, 100.1, 1700],
        [1678887240000, 100.1, 100.4, 99.8, 100.2, 1800],
        [1678887300000, 100.2, 100.5, 99.7, 99.9, 1900],
        [1678887360000, 99.9, 100.2, 99.5, 99.7, 2000],
        [1678887420000, 99.7, 100.0, 99.3, 99.5, 2100],
        [1678887480000, 99.5, 99.8, 99.0, 99.2, 2200],
        [1678887540000, 99.2, 99.5, 98.8, 99.0, 2300],
    ]
    df_ohlcv = pd.DataFrame(ohlcv_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_ohlcv['timestamp'] = pd.to_datetime(df_ohlcv['timestamp'], unit='ms')
    df_ohlcv.set_index('timestamp', inplace=True)

    # Create a dummy CSV file for historical liquidation events
    dummy_liquidation_csv_path = "dummy_historical_liquidations.csv"
    with open(dummy_liquidation_csv_path, "w", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "price", "qty", "side", "exchange", "symbol", "order_id"])
        writer.writerow([1678886400000 + 500, 100.05, 10.0, "LONG", "bybit", "SOL/USDT", "liq_1"])
        writer.writerow([1678886400000 + 1000, 100.00, 15.0, "SHORT", "bybit", "SOL/USDT", "liq_2"])
        writer.writerow([1678886580000 + 500, 100.45, 20.0, "LONG", "bybit", "SOL/USDT", "liq_3"])
        writer.writerow([1678886880000 + 500, 101.65, 25.0, "SHORT", "bybit", "SOL/USDT", "liq_4"])
        for i in range(3): # Simulate a sweep near the buy signal
            writer.writerow([1678886580000 + 100 + i*10, 100.4, 50.0 + i*10, "SHORT", "bybit", "SOL/USDT", f"sweep_liq_{i}"])

    # --- Grid Search Parameters ---
    rsi_lengths = range(Config.RSI_LENGTH - 5, Config.RSI_LENGTH + 6, 5)  # Example: 13, 18, 23
    oversold_levels = range(Config.RSI_OVERSOLD - 5, Config.RSI_OVERSOLD + 6, 5) # Example: 29, 34, 39
    overbought_levels = range(Config.RSI_OVERBOUGHT - 5, Config.RSI_OVERBOUGHT + 6, 5) # Example: 72, 77, 82
    stop_losses = np.arange(Config.STOP_LOSS_PERCENT - 0.01, Config.STOP_LOSS_PERCENT + 0.011, 0.01) # Example: 0.025, 0.035, 0.045
    take_profits = np.arange(Config.TAKE_PROFIT_PERCENT - 0.01, Config.TAKE_PROFIT_PERCENT + 0.011, 0.01) # Example: 0.04, 0.05, 0.06
    
    # New parameters for cluster and sweep
    bin_pct_range = np.arange(Config.BIN_PCT - 0.001, Config.BIN_PCT + 0.0011, 0.001) # Example: 0.001, 0.002, 0.003
    sweep_thresh_factors = np.arange(Config.SWEEP_THRESHOLD_FACTOR - 0.5, Config.SWEEP_THRESHOLD_FACTOR + 0.51, 0.5) # Example: 1.5, 2.0, 2.5
    min_sweep_volumes = np.arange(Config.MIN_SWEEP_VOLUME_USDT - 20, Config.MIN_SWEEP_VOLUME_USDT + 21, 20) # Example: 30, 50, 70


    all_combinations = list(itertools.product(
        rsi_lengths, oversold_levels, overbought_levels, stop_losses, take_profits,
        bin_pct_range, sweep_thresh_factors, min_sweep_volumes
    ))
    print(f"Total combinations to test: {len(all_combinations)}")

    # --- Run Grid Search in Parallel ---
    print("Starting parallel grid search...")
    pool = multiprocessing.Pool(processes=os.cpu_count()) # Use all available CPU cores
    
    # Create a partial function to pass fixed arguments to the worker function
    worker_func = partial(run_single_backtest_sync, df_ohlcv=df_ohlcv, historical_liquidation_file=dummy_liquidation_csv_path)
    
    # Use map to distribute combinations to worker processes with a progress bar
    all_results_nested = list(tqdm(pool.imap(worker_func, all_combinations), total=len(all_combinations), desc="Running Grid Search"))
    pool.close()
    pool.join()

    results = []
    for res in all_results_nested:
        if res: # Check if res is not None
            results.append(res)

    results_df = pd.DataFrame(results)
    results_df.to_csv('results.csv', index=False)
    print("\nGrid search completed. Results saved to results.csv")

    # --- Analyze and Plot Best Runs ---
    if not results_df.empty:
        # Filter out rows with no trades for meaningful ranking
        tradable_results = results_df[results_df['total_trades'] > 0]
        
        if not tradable_results.empty:
            # Rank by Sharpe ratio
            best_results = tradable_results.sort_values(by='sharpe_ratio', ascending=False).head(5)

            print("\nBest parameters (ranked by Sharpe Ratio):")
            print(best_results)

            # Plotting equity curves for best runs (simplified for this example)
            # This would require re-running the backtest for each best parameter set
            # For a real scenario, the backtest_engine should return the equity curve
            # For now, we'll just print the best parameters.
            
        else:
            print("\nNo tradable results found to analyze.")
    else:
        print("\nNo results were generated by the grid search.")

    os.remove(dummy_liquidation_csv_path) # Clean up dummy liquidation file