"""
Run backtest on SOLUSDT_1h.csv with the multi-factor strategy.
Gives us real win rate, Sharpe, and drawdown numbers — not fabricated ones.
"""
import pandas as pd, asyncio, logging, os, sys

os.environ['LOG_LEVEL'] = 'ERROR'
logging.getLogger().setLevel(logging.ERROR)
for l in ['src', 'root']: logging.getLogger(l).setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.config import Config
from src.backtest.backtest_engine import BacktestEngine

df = pd.read_csv('SOLUSDT_1h.csv', parse_dates=['timestamp'], index_col='timestamp')
print(f"Data: {len(df)} candles, {df.index[0]} to {df.index[-1]}")
print(f"Date range: {df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}")
print()

config = Config()
config.SYMBOLS = ['SOL/USDT']
config.RSI_LENGTH = 14
config.RSI_OVERSOLD = 30
config.RSI_OVERBOUGHT = 70
config.POSITION_USDT = 100
config.STOP_LOSS_PERCENT = 0.035
config.TAKE_PROFIT_PERCENT = 0.05
config.ENABLE_REGIME_FILTER = False
config.ENABLE_RSI_EXIT = True
Config.SIM_MODE = True

async def main():
    engine = BacktestEngine(config, df)
    r = await engine.run_backtest()
    
    keys = ['symbol', 'total_trades', 'winning_trades', 'losing_trades',
            'win_rate_pct', 'profit_factor', 'sharpe_ratio', 'sortino_ratio',
            'max_drawdown_pct', 'strategy_return_pct', 'buyhold_return_pct',
            'alpha_pct', 'avg_win', 'avg_loss', 'candles_processed']
    
    print("BACKTEST RESULTS")
    print("=" * 60)
    for k in keys:
        v = r.get(k, 'N/A')
        if isinstance(v, float): v = f"{v:.4f}"
        print(f"  {k:25s}: {v}")
    
    sig = r.get('signal_breakdown', {})
    print(f"\n  Signal breakdown: {sig}")
    ext = r.get('exit_reason_breakdown', {})
    print(f"  Exit reasons:      {ext}")
    
    print(f"\n  Trade log entries: {len(r.get('trade_log', []))}")
    trade_log = r.get('trade_log', [])
    for t in trade_log:
        s = f"    {t['Timestamp']} {t['Trade Type']:5s} {t['Signal Type']:25s} price={t['Price']:.2f}"
        if t.get('PNL') is not None: s += f" PnL={t['PNL']:.4f}"
        if t.get('Reason'): s += f" reason={t['Reason']}"
        print(s)

asyncio.run(main())
