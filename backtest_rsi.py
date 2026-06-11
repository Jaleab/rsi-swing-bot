import pandas as pd
import ta
import numpy as np

def run_backtest(df, rsi_length, oversold_level, overbought_level, stop_loss_percent, take_profit_percent, rsi_series=None):
    """
    Runs a backtest on the given DataFrame with the specified RSI and exit parameters.

    Args:
        df (pd.DataFrame): DataFrame with OHLCV data and a 'close' column.
        rsi_length (int): The period for the RSI calculation.
        oversold_level (int): RSI level considered oversold.
        overbought_level (int): RSI level considered overbought.
        stop_loss_percent (float): Percentage for stop loss (e.g., 0.02 for 2%).
        take_profit_percent (float): Percentage for take profit (e.g., 0.05 for 5%).
        rsi_series (pd.Series, optional): Pre-calculated RSI series. If provided, rsi_length is ignored for RSI calculation.

    Returns:
        pd.DataFrame: DataFrame with trade signals and equity curve.
    """
    df_copy = df.copy()
    
    # Calculate RSI or use pre-calculated series
    if rsi_series is not None:
        df_copy['rsi'] = rsi_series
    else:
        df_copy['rsi'] = ta.momentum.RSIIndicator(df_copy['close'], window=rsi_length).rsi()

    # Initialize columns for trading signals and positions
    df_copy['signal'] = 0  # 1 for buy, -1 for sell, 0 for hold
    df_copy['position'] = 0 # 1 for long, 0 for flat
    df_copy['entry_price'] = np.nan
    df_copy['exit_price'] = np.nan
    df_copy['pnl'] = 0.0

    in_position = False
    entry_price = 0
    stop_loss_price = 0
    take_profit_price = 0

    # Ensure the DataFrame is not empty before iterating
    if df_copy.empty:
        return df_copy

    for i in range(1, len(df_copy)):
        current_close = df_copy['close'].iloc[i]
        current_low = df_copy['low'].iloc[i]
        current_high = df_copy['high'].iloc[i]
        current_rsi = df_copy['rsi'].iloc[i]
        prev_rsi = df_copy['rsi'].iloc[i-1]

        # If not in position and RSI crosses up from oversold
        if not in_position and prev_rsi <= oversold_level and current_rsi > oversold_level:
            df_copy.loc[df_copy.index[i], 'signal'] = 1 # Buy signal
            df_copy.loc[df_copy.index[i], 'position'] = 1
            in_position = True
            entry_price = current_close
            df_copy.loc[df_copy.index[i], 'entry_price'] = entry_price
            stop_loss_price = entry_price * (1 - stop_loss_percent)
            take_profit_price = entry_price * (1 + take_profit_percent)
            
        # If in position
        elif in_position:
            df_copy.loc[df_copy.index[i], 'position'] = 1 # Maintain position

            # Check for Stop Loss
            if current_low <= stop_loss_price:
                df_copy.loc[df_copy.index[i], 'signal'] = -1 # Sell signal (stop loss hit)
                df_copy.loc[df_copy.index[i], 'exit_price'] = stop_loss_price
                df_copy.loc[df_copy.index[i], 'pnl'] = (stop_loss_price - entry_price) / entry_price
                in_position = False
            
            # Check for Take Profit
            elif current_high >= take_profit_price:
                df_copy.loc[df_copy.index[i], 'signal'] = -1 # Sell signal (take profit hit)
                df_copy.loc[df_copy.index[i], 'exit_price'] = take_profit_price
                df_copy.loc[df_copy.index[i], 'pnl'] = (take_profit_price - entry_price) / entry_price
                in_position = False
                
            # Check for RSI cross down from overbought
            elif prev_rsi >= overbought_level and current_rsi < overbought_level:
                df_copy.loc[df_copy.index[i], 'signal'] = -1 # Sell signal (RSI exit)
                df_copy.loc[df_copy.index[i], 'exit_price'] = current_close
                df_copy.loc[df_copy.index[i], 'pnl'] = (current_close - entry_price) / entry_price
                in_position = False

    # Calculate cumulative PnL for equity curve
    df_copy['cumulative_pnl'] = (1 + df_copy['pnl']).cumprod()
    if not df_copy.empty and df_copy['cumulative_pnl'].iloc[0] == 0:
        df_copy['cumulative_pnl'].iloc[0] = 1 # Starting equity

    return df_copy

if __name__ == "__main__":
    # This part is for testing the backtest_rsi.py independently
    # In a real scenario, data would be fetched using fetch_candles.py and loaded here.
    try:
        df_test = pd.read_csv('SOLUSDT_1h.csv', index_col='timestamp', parse_dates=True)
        print("Successfully loaded SOLUSDT_1h.csv")
    except FileNotFoundError:
        print("SOLUSDT_1h.csv not found. Please run fetch_candles.py first to generate data.")
        exit()

    # Example parameters
    rsi_len = 14
    oversold = 30
    overbought = 70
    sl_pct = 0.02
    tp_pct = 0.05

    print(f"\nRunning backtest with: RSI Length={rsi_len}, Oversold={oversold}, Overbought={overbought}, SL={sl_pct*100}%, TP={tp_pct*100}%")
    results_df = run_backtest(df_test, rsi_len, oversold, overbought, sl_pct, tp_pct)

    print("\nBacktest Results (first 5 rows):")
    print(results_df.head())
    print("\nBacktest Results (last 5 rows):")
    print(results_df.tail())

    total_trades = results_df[results_df['signal'] == -1].shape[0]
    winning_trades = results_df[results_df['pnl'] > 0].shape[0]
    losing_trades = results_df[results_df['pnl'] < 0].shape[0]

    if total_trades > 0:
        win_rate = winning_trades / total_trades * 100
        print(f"\nTotal Trades: {total_trades}")
        print(f"Winning Trades: {winning_trades}")
        print(f"Losing Trades: {losing_trades}")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Total PnL: {(results_df['cumulative_pnl'].iloc[-1] - 1) * 100:.2f}%")
    else:
        print("\nNo trades were executed in this backtest.")

    # Simple equity curve plot
    import matplotlib.pyplot as plt
    plt.figure(figsize=(12, 6))
    results_df['cumulative_pnl'].plot(title="Equity Curve")
    plt.xlabel("Date")
    plt.ylabel("Cumulative PnL")
    plt.grid(True)
    plt.savefig('equity_curve_single_run.png')
    print("\nEquity curve saved to equity_curve_single_run.png")