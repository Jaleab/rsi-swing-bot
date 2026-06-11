print("Script is starting...")
import pandas as pd
import numpy as np
import asyncio
import os
import csv
from datetime import datetime
from typing import Dict, Any, Literal, TypedDict, Optional, List
from unittest.mock import MagicMock

from src.config import Config
from src.rsi_calc import calculate_rsi
from src.cluster_aggregator import ClusterAggregator, LiquidationEvent
from src.signal_generator import SignalGenerator
from src.backtest.cluster_reconstruction import ClusterReconstructor
from src.position import Position

TAKER_FEE_RATE = 0.00055  # 0.055% taker fee (Bybit standard)
SLIPPAGE_BPS = 1.0        # 1 basis point slippage per trade

class BacktestEngine:
    def __init__(self, config: Config, historical_ohlcv: pd.DataFrame, historical_liquidation_file: Optional[str] = None):
        self.config = config
        self.ohlcv = historical_ohlcv
        self.taker_fee = TAKER_FEE_RATE
        self.slippage_pct = SLIPPAGE_BPS / 10000
        self.initial_balance = config.POSITION_USDT * 10  # Simulated starting capital
        from src.position_manager import PositionManager
        from src.abstract_exchange import AbstractExchangeClient
        from src.status_tracker import PairStatus, StatusTracker
        from src.guards import GuardResult

        class MockExchangeClient(AbstractExchangeClient):
            def __init__(self):
                super().__init__("","",False)
            async def get_balance(self, currency='USDT'): return {'total': 100000.0, 'free': 100000.0, 'used': 0.0}
            async def place_order(self, *args, **kwargs): return {}
            async def cancel_order(self, *args, **kwargs): return {}
            async def get_open_orders(self, *args, **kwargs): return []
            async def get_order_status(self, *args, **kwargs): return {}
            async def get_positions(self, *args, **kwargs): return []
            async def get_order_book(self, *args, **kwargs): return {'bids':[], 'asks':[]}
            async def get_recent_trades(self, *args, **kwargs): return []
            async def execute_order(self, *args, **kwargs): return {}
            async def fetch_ohlcv(self, *args, **kwargs): return []

        class MockStatusTracker:
            def __init__(self, symbol: str):
                self.status: Dict[str, PairStatus] = {symbol: PairStatus(symbol=symbol)}
                self.position_managers = {}
            def update_status(self, **kwargs): pass
            def update_guard_metrics(self, *args, **kwargs): pass
            def increment_error(self, *args, **kwargs): pass

        symbol = self.config.SYMBOLS[0] if self.config.SYMBOLS else "SOL/USDT"
        mock_status_tracker = MockStatusTracker(symbol)
        mock_metrics = MagicMock()
        self.cluster_aggregator = ClusterAggregator(config, mock_status_tracker)
        self.signal_generator = SignalGenerator(config, self.cluster_aggregator)
        self.cluster_reconstructor = ClusterReconstructor(config, self.cluster_aggregator)
        self.position_manager = PositionManager(config, MockExchangeClient(), mock_metrics, mock_status_tracker)

        if historical_liquidation_file:
            self.cluster_reconstructor.load_historical_events(historical_liquidation_file)
        
        self.trade_log = []
        self.signal_log = []
        self.equity_curve = []  # [(timestamp, equity_value), ...]
        self.current_equity = self.initial_balance

    def _deduct_fees(self, entry_amount: float, entry_price: float, exit_amount: float, exit_price: float) -> float:
        """Calculate total fees + slippage for a round-trip trade."""
        entry_fee = entry_amount * entry_price * self.taker_fee
        exit_fee = exit_amount * exit_price * self.taker_fee
        slip_cost = (entry_amount * entry_price + exit_amount * exit_price) * self.slippage_pct
        return entry_fee + exit_fee + slip_cost

    def _log_backtest_trade(self, symbol, timestamp, trade_type, signal_type, price, amount,
                            confidence="N/A", reason="N/A", target_price=None, stop_price=None,
                            pnl=None, entry_price=None, source="RSI_ONLY"):
        trade_entry = {
            'Symbol': symbol, 'Timestamp': timestamp, 'Mode': "BACKTEST",
            'Trade Type': trade_type, 'Signal Type': signal_type,
            'Price': price, 'Amount': amount, 'Confidence': confidence,
            'Reason': reason, 'TP': target_price, 'SL': stop_price,
            'PNL': round(pnl, 4) if pnl is not None else None,
            'Entry_Price': entry_price,
            'Source': source
        }
        self.trade_log.append(trade_entry)
        if pnl is not None:
            self.current_equity += pnl
            self.equity_curve.append({"timestamp": timestamp, "equity": self.current_equity})

    async def run_backtest(self):
        symbol = self.config.SYMBOLS[0] if self.config.SYMBOLS else "SOL/USDT"
        print(f"Running backtest for {symbol} with {len(self.ohlcv)} candles...")

        # Calculate RSI for the entire OHLCV data once
        self.ohlcv['rsi'] = calculate_rsi(self.ohlcv, length=self.config.RSI_LENGTH)

        for i in range(len(self.ohlcv)):
            current_candle = self.ohlcv.iloc[i]
            try:
                current_timestamp_ms = int(current_candle.name.timestamp() * 1000)
            except AttributeError:
                current_timestamp_ms = int(current_candle.name.value / 1_000_000)
            current_price = current_candle['close']
            
            # Replay liquidation events up to the current candle's timestamp
            await self.cluster_reconstructor.replay_events_up_to_timestamp(current_timestamp_ms)
            
            # Get cluster snapshot (sync call, not async)
            cluster_snapshot = self.cluster_aggregator.get_snapshot(symbol, current_time_ms_override=current_timestamp_ms)
            is_liquidation_data_available = bool(cluster_snapshot.get("clusters"))

            # Get signal
            trading_signal = self.signal_generator.decide(
                symbol=symbol,
                current_price=current_price,
                ohlcv_df=self.ohlcv.iloc[:i+1],
                cluster_snapshot=cluster_snapshot,
                is_liquidation_data_available=is_liquidation_data_available,
                is_sweep=self.cluster_reconstructor.current_sweep_data.get('is_sweep', False),
                bullish_sweep_volume=self.cluster_reconstructor.current_sweep_data.get('bullish_sweep_volume', 0.0),
                bearish_sweep_volume=self.cluster_reconstructor.current_sweep_data.get('bearish_sweep_volume', 0.0)
            )

            # --- Trading Logic ---
            signal_type = trading_signal.get('signal_type', 'NEUTRAL')
            is_long_signal = 'LONG' in signal_type and signal_type != 'NEUTRAL'
            is_short_signal = 'SHORT' in signal_type and signal_type != 'NEUTRAL'
            current_position = self.position_manager.get_open_position(symbol)

            # Entry
            if is_long_signal and not current_position:
                guard_result = self.position_manager.can_open_any_position()
                if guard_result.allowed:
                    amount_usdt = self.config.POSITION_USDT
                    amount = amount_usdt / current_price
                    
                    top_clusters = cluster_snapshot.get("top_clusters", [])
                    support_price = None
                    resistance_price = None
                    if top_clusters:
                        tc = top_clusters[0]
                        if tc["centroid_price"] < current_price:
                            support_price = tc["centroid_price"]
                        else:
                            resistance_price = tc["centroid_price"]
                    
                    sl_tp = self.position_manager.calculate_dynamic_sl_tp(current_price, 1, support_price, resistance_price)
                    
                    position = Position(
                        symbol=symbol, size=amount, entry_price=current_price,
                        position_type='long', timestamp=current_timestamp_ms,
                        target_price=sl_tp['target_price'], stop_price=sl_tp['stop_price'],
                        state='OPEN'
                    )
                    self.position_manager.update_position(symbol, position)
                    self._log_backtest_trade(
                        symbol,
                        datetime.fromtimestamp(current_timestamp_ms / 1000).isoformat(),
                        'ENTRY', signal_type, current_price, amount,
                        confidence=trading_signal.get('confidence_score', 0),
                        reason=trading_signal.get('reason', ''),
                        target_price=sl_tp['target_price'], stop_price=sl_tp['stop_price'],
                        entry_price=current_price,
                        source=trading_signal.get('reason', '')
                    )
                else:
                    pass

            # Exit
            elif current_position:
                sl_hit = current_position.stop_price and current_price <= current_position.stop_price
                tp_hit = current_position.target_price and current_price >= current_position.target_price
                rsi_exit = is_short_signal
                
                if sl_hit or tp_hit or rsi_exit:
                    exit_price = current_position.stop_price if sl_hit else (current_position.target_price if tp_hit else current_price)
                    gross_pnl = (exit_price - current_position.entry_price) * current_position.size
                    fees = self._deduct_fees(current_position.size, current_position.entry_price,
                                             current_position.size, exit_price)
                    net_pnl = gross_pnl - fees
                    exit_reason = "RSI_Exit"
                    if sl_hit:
                        exit_reason = "SL_Hit"
                    elif tp_hit:
                        exit_reason = "TP_Hit"
                    
                    self._log_backtest_trade(
                        symbol,
                        datetime.fromtimestamp(current_timestamp_ms / 1000).isoformat(),
                        'EXIT', 'Exit', exit_price, current_position.size,
                        pnl=net_pnl, reason=exit_reason,
                        target_price=current_position.target_price,
                        stop_price=current_position.stop_price,
                        entry_price=current_position.entry_price,
                        source=f"{exit_reason} | gross={gross_pnl:.2f} fees={fees:.2f}"
                    )
                    
                    current_position.close_position(
                        closing_price=exit_price,
                        closing_timestamp=current_timestamp_ms,
                        close_reason=exit_reason
                    )
                    self.position_manager.remove_position(symbol)
                    current_position = None
            
            # Update unrealized PnL if in position
            if current_position:
                current_position.update_unrealized_pnl(current_price)
            
            # Log signal
            self._log_signal(current_timestamp_ms, current_price, trading_signal)

            await asyncio.sleep(0)

        self._save_signal_log_to_csv("signal_log.csv")
        self._save_trade_log_to_csv()
        summary = self.calculate_metrics()
        summary["trade_log"] = self.trade_log
        summary["signal_log"] = self.signal_log
        return summary

    def calculate_metrics(self) -> Dict[str, Any]:
        """Calculate comprehensive performance metrics from trade log."""
        exit_trades = [t for t in self.trade_log if t['Trade Type'] == 'EXIT']
        pnls = [t['PNL'] for t in exit_trades if t['PNL'] is not None]

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_pnl = sum(pnls)
        total_trades = len(pnls)
        win_rate = len(wins) / total_trades if total_trades > 0 else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        total_wins_pnl = sum(wins) if wins else 0
        total_losses_pnl = abs(sum(losses)) if losses else 1
        profit_factor = total_wins_pnl / total_losses_pnl if total_losses_pnl > 0 else float('inf')

        # Equity curve
        if self.equity_curve:
            eq_series = pd.Series([e["equity"] for e in self.equity_curve])
            returns = eq_series.pct_change().dropna()
            sharpe = (returns.mean() * 252) / (returns.std() * np.sqrt(252)) if returns.std() > 0 and len(returns) > 1 else 0
            sortino_std = returns[returns < 0].std()
            sortino = (returns.mean() * 252) / (sortino_std * np.sqrt(252)) if sortino_std and sortino_std > 0 and len(returns) > 1 else 0
            peak = eq_series.expanding().max()
            drawdown_series = (eq_series - peak) / peak
            max_dd = drawdown_series.min()
        else:
            sharpe = 0
            sortino = 0
            max_dd = 0

        # Benchmark: buy-and-hold
        if len(self.ohlcv) > 1:
            bh_initial = self.ohlcv['close'].iloc[0]
            bh_final = self.ohlcv['close'].iloc[-1]
            bh_return = (bh_final - bh_initial) / bh_initial * 100
        else:
            bh_return = 0

        strategy_return = (self.current_equity - self.initial_balance) / self.initial_balance * 100

        # Entry signal breakdown
        signal_counts = {}
        for t in self.trade_log:
            if t['Trade Type'] == 'ENTRY':
                sig = t.get('Signal Type', 'Unknown')
                signal_counts[sig] = signal_counts.get(sig, 0) + 1

        # Exit reason breakdown
        exit_reasons = {}
        for t in exit_trades:
            reason = t.get('Reason', 'Unknown')
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        return {
            "symbol": self.config.SYMBOLS[0] if self.config.SYMBOLS else "SOL/USDT",
            "initial_balance": self.initial_balance,
            "final_equity": round(self.current_equity, 2),
            "total_pnl": round(total_pnl, 2),
            "strategy_return_pct": round(strategy_return, 2),
            "buyhold_return_pct": round(bh_return, 2),
            "alpha_pct": round(strategy_return - bh_return, 2),
            "total_trades": total_trades,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": round(win_rate * 100, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 3),
            "sortino_ratio": round(sortino, 3),
            "max_drawdown_pct": round(max_dd * 100, 2) if max_dd else 0,
            "signal_breakdown": signal_counts,
            "exit_reason_breakdown": exit_reasons,
            "candles_processed": len(self.ohlcv),
        }

    def _save_trade_log_to_csv(self):
        if not self.trade_log:
            return
        keys = self.trade_log[0].keys()
        with open("backtest_trades.csv", 'w', newline='') as f:
            w = csv.DictWriter(f, keys)
            w.writeheader()
            w.writerows(self.trade_log)

    def _log_signal(self, timestamp: int, mark_price: float, signal: Dict[str, Any]):
        signal_entry = {
            'timestamp': datetime.fromtimestamp(timestamp / 1000).isoformat(),
            'mark_price': mark_price,
            'signal_type': signal['signal_type'],
            'confidence_score': signal['confidence_score'],
            'reason': signal['reason'],
            'rsi_value': signal['rsi_value'],
            'rsi_signal': signal['rsi_signal'],
            'cluster_impact_score': signal['cluster_impact_score'],
            'proximity_score': signal['proximity_score'],
            'cluster_dominance_score': signal['cluster_dominance_score'],
            'is_sweep': signal['is_sweep'],
            'sweep_volume_usdt': (signal.get('bullish_sweep_volume', 0) + signal.get('bearish_sweep_volume', 0)),
        }
        self.signal_log.append(signal_entry)

    def _save_signal_log_to_csv(self, filename: str):
        if not self.signal_log:
            print("No signals to save to CSV.")
            return

        keys = self.signal_log[0].keys()
        with open(filename, 'w', newline='') as output_file:
            dict_writer = csv.DictWriter(output_file, keys)
            dict_writer.writeheader()
            dict_writer.writerows(self.signal_log)
        print(f"Signal log saved to {filename}")


# Example Usage (for testing)
async def main_test():
    print("Starting main_test function (in-memory)...")
    import logging
    logging.basicConfig(level=logging.INFO) # Set logging level to INFO for test output
    
    # Remove previous signal_log.csv if it exists
    if os.path.exists("signal_log.csv"):
        os.remove("signal_log.csv")
        print("Removed existing signal_log.csv")
    
    # Remove previous signal_log.csv if it exists
    if os.path.exists("signal_log.csv"):
        os.remove("signal_log.csv")
        print("Removed existing signal_log.csv")

    # Load realistic OHLCV data from CSV
    try:
        df_ohlcv = pd.read_csv(
            "SOLUSDT_1h.csv",
            parse_dates=['timestamp'],
            index_col='timestamp',
            dtype={
                'open': float, 'high': float, 'low': float, 'close': float,
                'volume': float, 'turnover': float, 'trades': int
            }
        )
        print(f"Loaded {len(df_ohlcv)} candles from SOLUSDT_1h.csv")
    except FileNotFoundError:
        print("Error: SOLUSDT_1h.csv not found. Please ensure the file is in the root directory.")
        return
    except Exception as e:
        print(f"Error loading SOLUSDT_1h.csv: {e}")
        return

    # Dummy historical liquidation file (no longer created or removed)
    dummy_liquidation_csv_path = "dummy_historical_liquidations.csv"

    # Initialize Config for backtest
    config = Config()
    config.SYMBOLS = ["SOL/USDT"]
    config.RSI_LENGTH = 14
    config.POSITION_USDT = 100 # Example position size
    config.RISK_PER_TRADE_PERCENT = 0.01 # Example risk
    config.USE_DYNAMIC_SLTP = True # Enable dynamic SL/TP for testing
    config.LIVE_TRADING = False # Ensure live trading is disabled for backtest
    
    # We need to temporarily set SIM_MODE to True for backtesting to use the simulated path in PositionManager
    Config.SIM_MODE = True
    backtest_engine = BacktestEngine(config, df_ohlcv, dummy_liquidation_csv_path)
    summary = await backtest_engine.run_backtest()
    Config.SIM_MODE = False

    print("\n========== Backtest Results ==========")
    for key, value in summary.items():
        if key in ('trade_log', 'signal_log', 'signal_breakdown', 'exit_reason_breakdown'):
            print(f"\n--- {key} ---")
            if isinstance(value, dict):
                for k, v in value.items():
                    print(f"  {k}: {v}")
            elif isinstance(value, list):
                print(f"  {len(value)} entries")
                for t in value[-5:]:
                    print(f"  {t}")
        else:
            print(f"  {key}: {value}")
    print("========== Backtest Complete ==========")