import pandas as pd
import os

TRADE_LOG_FILE = "data/live/live/trade_log.csv"

def generate_trade_report():
    if not os.path.exists(TRADE_LOG_FILE):
        print(f"Trade log file not found: {TRADE_LOG_FILE}")
        return

    df = pd.read_csv(TRADE_LOG_FILE)

    if df.empty:
        print("No trades recorded yet.")
        return

    total_pnl = df['realized_pnl'].sum()
    num_trades = len(df)
    winning_trades = df[df['realized_pnl'] > 0]
    num_winning_trades = len(winning_trades)
    win_rate = (num_winning_trades / num_trades) * 100 if num_trades > 0 else 0

    average_pnl_per_trade = total_pnl / num_trades if num_trades > 0 else 0
    average_hold_time_seconds = df['hold_duration_seconds'].mean()

    print("\n--- Trade Performance Report ---")
    print(f"Total Trades: {num_trades}")
    print(f"Total Realized PnL: {total_pnl:.2f}")
    print(f"Number of Winning Trades: {num_winning_trades}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Average PnL per Trade: {average_pnl_per_trade:.2f}")
    print(f"Average Hold Time: {average_hold_time_seconds:.2f} seconds")
    print("--------------------------------\n")

if __name__ == "__main__":
    generate_trade_report()