print("Script is starting...") # Added debug print at the very beginning
import pandas as pd
import asyncio
import os
import csv
from datetime import datetime
from typing import Dict, Any, Literal, TypedDict, Optional

from src.config import Config
from src.rsi_calc import calculate_rsi
from src.cluster_aggregator import ClusterAggregator, LiquidationEvent # Assuming LiquidationEvent is defined here or common
from src.signal_generator import SignalGenerator, Signal
from src.backtest.cluster_reconstruction import ClusterReconstructor

class BacktestEngine:
    def __init__(self, config: Config, historical_ohlcv: pd.DataFrame, historical_liquidation_file: Optional[str] = None):
        self.config = config
        self.ohlcv = historical_ohlcv
        # In backtesting, we don't have live OrderBookManager and TradeStreamManager.
        # SignalGenerator needs to be adapted for backtesting by not requiring them.
        # For now, we'll pass None and update SignalGenerator init for backtest mode
        self.cluster_aggregator = ClusterAggregator(config)
        self.signal_generator = SignalGenerator(config, self.cluster_aggregator)
        self.cluster_reconstructor = ClusterReconstructor(config, self.cluster_aggregator)
        from src.position_manager import PositionManager, Position
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
        self.cluster_aggregator = ClusterAggregator(config, mock_status_tracker)
        self.signal_generator = SignalGenerator(config, self.cluster_aggregator)
        self.cluster_reconstructor = ClusterReconstructor(config, self.cluster_aggregator)
        self.position_manager = PositionManager(config, MockExchangeClient(), None, mock_status_tracker)

        if historical_liquidation_file:
            self.cluster_reconstructor.load_historical_events(historical_liquidation_file)
        
        self.trade_log = [] # In-memory list to store trade logs
        self.signal_log = [] # In-memory list to store all generated signals
        self.metrics = {
            "cumulative_pnl": 0.0,
            "max_drawdown": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "equity_curve": [], # To store equity curve over time
        }

    def _log_backtest_trade(self, symbol, timestamp, trade_type, signal_type, price, amount, confidence="N/A", reason="N/A", target_price=None, stop_price=None, pnl=None, source="RSI_ONLY"):
        trade_entry = {
            'Symbol': symbol,
            'Timestamp': timestamp,
            'Mode': "BACKTEST",
            'Trade Type': trade_type,
            'Signal Type': signal_type,
            'Price': price,
            'Amount': amount,
            'Confidence': confidence,
            'Reason': reason,
            'TP': target_price,
            'SL': stop_price,
            'PNL': pnl,
            'Source': source
        }
        self.trade_log.append(trade_entry)
        # Update in-memory metrics
        if pnl is not None:
            self.metrics["cumulative_pnl"] += pnl
            self.metrics["total_trades"] += 1
            if pnl > 0:
                self.metrics["winning_trades"] += 1
            else:
                self.metrics["losing_trades"] += 1
            
            # Update equity curve (for plotting later)
            self.metrics["equity_curve"].append({"timestamp": timestamp, "cumulative_pnl": self.metrics["cumulative_pnl"]})

    async def run_backtest(self):
        symbol = self.config.SYMBOLS[0] if self.config.SYMBOLS else "SOL/USDT"
        print(f"Running backtest for {symbol} with {len(self.ohlcv)} candles...")

        # Calculate RSI for the entire OHLCV data once
        self.ohlcv['rsi'] = calculate_rsi(self.ohlcv, self.config.RSI_LENGTH)

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
                        'LONG', signal_type, current_price, amount,
                        confidence=trading_signal.get('confidence_score', 0),
                        reason=trading_signal.get('reason', ''),
                        target_price=sl_tp['target_price'], stop_price=sl_tp['stop_price'],
                        source=trading_signal.get('reason', '')
                    )
                    print(f"Backtest: LONG Entry at {current_price:.2f}. Signal: {signal_type}")
                else:
                    print(f"Backtest: Cannot open position. {guard_result.reason}")

            # Exit
            elif current_position:
                sl_hit = current_position.stop_price and current_price <= current_position.stop_price
                tp_hit = current_position.target_price and current_price >= current_position.target_price
                rsi_exit = is_short_signal  # Short signal while long = RSI reversal
                
                if sl_hit or tp_hit or rsi_exit:
                    pnl = (current_price - current_position.entry_price) * current_position.size
                    exit_reason = "RSI_Exit"
                    if sl_hit:
                        exit_reason = "SL_Hit"
                    elif tp_hit:
                        exit_reason = "TP_Hit"
                    
                    self.position_manager.remove_position(symbol)
                    self._log_backtest_trade(
                        symbol,
                        datetime.fromtimestamp(current_timestamp_ms / 1000).isoformat(),
                        'LONG', 'Exit', current_price, current_position.size,
                        pnl=pnl, reason=exit_reason,
                        target_price=current_position.target_price,
                        stop_price=current_position.stop_price,
                        source=trading_signal.get('reason', '')
                    )
                    print(f"Backtest: LONG Exit at {current_price:.2f}. PnL: {pnl:.2f}. Reason: {exit_reason}")
            
            # Update unrealized PnL if in position
            if current_position:
                current_position.update_unrealized_pnl(current_price)
            
            # Log signal
            self._log_signal(current_timestamp_ms, current_price, trading_signal)

            await asyncio.sleep(0)

        self._save_signal_log_to_csv("signal_log.csv")
        return self.trade_log, self.metrics

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
    trade_log, metrics = await backtest_engine.run_backtest() # Capture returned results
    Config.SIM_MODE = False # Reset SIM_MODE after backtest

    print("\n--- Backtest Results ---")
    print("\nTrade Log:")
    for trade in trade_log:
        print(trade)
    
    print("\nPerformance Metrics:")
    for key, value in metrics.items():
        if key == "equity_curve":
            print(f"  {key}: {len(value)} data points")
        else:
            print(f"  {key}: {value}")

    print("\nBacktest test completed (in-memory).")