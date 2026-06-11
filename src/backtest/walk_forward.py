"""
Walk-forward validation harness for the RSI Swing Bot.

Splits historical data into N train/test windows, optimizes parameters on the
train window, evaluates on the test window, and rolls forward.
Produces aggregated out-of-sample performance metrics.
"""
import pandas as pd
import numpy as np
import asyncio
import itertools
import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional

from src.config import Config
from src.backtest.backtest_engine import BacktestEngine


def split_windows(df: pd.DataFrame, train_pct: float = 0.7, n_windows: int = 4) -> List[tuple]:
    """Split OHLCV into overlapping train/test windows for walk-forward."""
    n = len(df)
    test_size = int(n * (1 - train_pct) / n_windows)
    windows = []
    for i in range(n_windows):
        test_start = int(n * train_pct) + i * test_size
        test_end = test_start + test_size
        if test_end > n:
            test_end = n
        train_df = df.iloc[:test_start].copy()
        test_df = df.iloc[test_start:test_end].copy()
        if len(train_df) > 50 and len(test_df) > 10:
            windows.append((train_df, test_df))
    return windows


def evaluate_params(params: dict, ohlcv_df: pd.DataFrame, liquidation_file: Optional[str] = None) -> dict:
    """Run a backtest with given parameters and return metrics."""
    async def _run():
        config = Config()
        config.SYMBOLS = [params.get('symbol', 'SOL/USDT')]
        config.RSI_LENGTH = params.get('rsi_length', 14)
        config.RSI_OVERSOLD = params.get('rsi_oversold', 30)
        config.RSI_OVERBOUGHT = params.get('rsi_overbought', 70)
        config.STOP_LOSS_PERCENT = params.get('stop_loss_pct', 0.035)
        config.TAKE_PROFIT_PERCENT = params.get('take_profit_pct', 0.05)
        config.POSITION_USDT = params.get('position_usdt', 100)
        config.SIM_MODE = True
        config.ENABLE_REGIME_FILTER = params.get('enable_regime_filter', False)
        config.ENABLE_RSI_EXIT = params.get('enable_rsi_exit', True)

        engine = BacktestEngine(config, ohlcv_df, liquidation_file)
        return await engine.run_backtest()

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_run())
    finally:
        loop.close()
    return result


def grid_search_params(param_grid: dict, train_df: pd.DataFrame, metric: str = 'sharpe_ratio') -> dict:
    """Simple grid search over parameter combinations. Returns best params dict."""
    best_params = None
    best_score = -float('inf')

    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combinations = list(itertools.product(*values))

    for combo in combinations:
        params = dict(zip(keys, combo))
        result = evaluate_params(params, train_df)
        score = result.get(metric, 0)
        if score > best_score:
            best_score = score
            best_params = params

    return best_params or dict(zip(keys, [v[0] for v in values]))


def run_walk_forward(ohlcv_path: str, param_grid: Optional[dict] = None,
                     train_pct: float = 0.7, n_windows: int = 4,
                     metric: str = 'sharpe_ratio',
                     liquidation_file: Optional[str] = None,
                     output_path: str = "walk_forward_results.json") -> dict:
    """
    Run a full walk-forward validation.

    Args:
        ohlcv_path: Path to OHLCV CSV file (timestamp index, open/high/low/close/volume columns)
        param_grid: Dict of parameter names to lists of values. If None, uses default params.
        train_pct: Fraction of data for initial training
        n_windows: Number of walk-forward windows
        metric: Metric to optimize during grid search
        liquidation_file: Optional path to historical liquidation CSV
        output_path: Path to save JSON results

    Returns:
        Dict with aggregated out-of-sample metrics
    """
    print(f"Loading OHLCV data from {ohlcv_path}...")
    try:
        df = pd.read_csv(ohlcv_path, parse_dates=['timestamp'], index_col='timestamp')
    except Exception:
        df = pd.read_csv(ohlcv_path)
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)

    print(f"Loaded {len(df)} candles from {df.index[0]} to {df.index[-1]}")

    if param_grid is None:
        param_grid = {
            'rsi_length': [14],
            'rsi_oversold': [30],
            'rsi_overbought': [70],
            'stop_loss_pct': [0.035],
            'take_profit_pct': [0.05],
            'enable_regime_filter': [True],
            'enable_rsi_exit': [True],
        }

    windows = split_windows(df, train_pct, n_windows)
    print(f"Created {len(windows)} walk-forward windows")

    fold_results = []
    all_metrics = []

    for fold, (train_df, test_df) in enumerate(windows):
        print(f"\n--- Fold {fold+1}/{len(windows)} ---")
        print(f"  Train: {train_df.index[0]} -> {train_df.index[-1]} ({len(train_df)} candles)")
        print(f"  Test:  {test_df.index[0]} -> {test_df.index[-1]} ({len(test_df)} candles)")

        # Optimize on train
        best_params = grid_search_params(param_grid, train_df, metric)
        print(f"  Best params: {best_params}")

        # Evaluate on test with best params
        test_result = evaluate_params(best_params, test_df, liquidation_file)
        print(f"  Test Sharpe: {test_result.get('sharpe_ratio', 0):.3f}, "
              f"Return: {test_result.get('strategy_return_pct', 0):.2f}%, "
              f"Trades: {test_result.get('total_trades', 0)}")

        fold_results.append({
            "fold": fold + 1,
            "best_params": best_params,
            "test_metrics": {k: v for k, v in test_result.items()
                           if k not in ('trade_log', 'signal_log', 'signal_breakdown', 'exit_reason_breakdown')},
            "train_range": f"{train_df.index[0]} -> {train_df.index[-1]}",
            "test_range": f"{test_df.index[0]} -> {test_df.index[-1]}",
        })
        all_metrics.append(test_result)

    # Aggregate out-of-sample metrics
    oos_sharpes = [m.get('sharpe_ratio', 0) for m in all_metrics]
    oos_returns = [m.get('strategy_return_pct', 0) for m in all_metrics]
    oos_trades = [m.get('total_trades', 0) for m in all_metrics]
    oos_win_rates = [m.get('win_rate_pct', 0) for m in all_metrics]
    oos_max_dd = [m.get('max_drawdown_pct', 0) for m in all_metrics]

    summary = {
        "run_date": datetime.now().isoformat(),
        "ohlcv_path": ohlcv_path,
        "total_candles": len(df),
        "n_windows": len(windows),
        "metric_optimized": metric,
        "fold_details": fold_results,
        "aggregated_metrics": {
            "avg_sharpe": round(np.mean(oos_sharpes), 3) if oos_sharpes else 0,
            "min_sharpe": round(np.min(oos_sharpes), 3) if oos_sharpes else 0,
            "max_sharpe": round(np.max(oos_sharpes), 3) if oos_sharpes else 0,
            "avg_return_pct": round(np.mean(oos_returns), 2) if oos_returns else 0,
            "avg_win_rate_pct": round(np.mean(oos_win_rates), 2) if oos_win_rates else 0,
            "avg_max_dd_pct": round(np.mean(oos_max_dd), 2) if oos_max_dd else 0,
            "total_oos_trades": sum(oos_trades) if oos_trades else 0,
        },
        "verdict": None,
    }

    # Auto-verdict
    avg_s = summary["aggregated_metrics"]["avg_sharpe"]
    avg_r = summary["aggregated_metrics"]["avg_return_pct"]
    avg_dd = summary["aggregated_metrics"]["avg_max_dd_pct"]
    if avg_s >= 0.5 and avg_r > 0 and avg_dd < 20:
        summary["verdict"] = "PASS — Strategy shows positive out-of-sample expectancy"
    elif avg_s >= 0 and avg_r > 0:
        summary["verdict"] = "MARGINAL — Positive returns but low risk-adjusted performance"
    else:
        summary["verdict"] = "FAIL — Strategy does not show positive out-of-sample expectancy"

    with open(output_path, 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")
    print(f"Verdict: {summary['verdict']}")

    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Walk-forward validation for RSI Swing Bot")
    parser.add_argument("--ohlcv", default="SOLUSDT_1h.csv", help="Path to OHLCV CSV file")
    parser.add_argument("--windows", type=int, default=4, help="Number of walk-forward windows")
    parser.add_argument("--train-pct", type=float, default=0.7, help="Initial train fraction")
    parser.add_argument("--output", default="walk_forward_results.json", help="Output JSON path")
    parser.add_argument("--grid", action="store_true", help="Enable full grid search (slow)")
    args = parser.parse_args()

    param_grid = None
    if args.grid:
        param_grid = {
            'rsi_length': [7, 14, 21],
            'rsi_oversold': [25, 30, 35],
            'rsi_overbought': [65, 70, 75],
            'stop_loss_pct': [0.02, 0.035, 0.05],
            'take_profit_pct': [0.03, 0.05, 0.07],
            'enable_regime_filter': [True, False],
            'enable_rsi_exit': [True, False],
        }

    run_walk_forward(
        ohlcv_path=args.ohlcv,
        param_grid=param_grid,
        train_pct=args.train_pct,
        n_windows=args.windows,
        output_path=args.output,
    )
