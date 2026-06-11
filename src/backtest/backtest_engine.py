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
        # PositionManager also needs a mock exchange client for backtesting
        from src.position_manager import PositionManager
        from src.abstract_exchange import AbstractExchangeClient
        from src.status_tracker import PairStatus, StatusTracker # Import PairStatus and StatusTracker

        class MockExchangeClient(AbstractExchangeClient):
            def __init__(self):
                super().__init__("","",False) # Dummy values
            async def get_balance(self, currency: str = 'USDT') -> Dict[str, Any]: return {'total': 100000.0, 'free': 100000.0, 'used': 0.0}
            async def place_order(self, symbol: str, side: str, order_type: str, quantity: float, price: Optional[float] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]: return {}
            async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]: return {}
            async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]: return []
            async def get_order_status(self, order_id: str, symbol: str) -> Dict[str, Any]: return {}
            async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]: return []
            async def get_order_book(self, symbol: str, limit: Optional[int] = None) -> Dict[str, Any]: return {'bids': [], 'asks': []}
            async def get_recent_trades(self, symbol: str, limit: Optional[int] = None) -> List[Dict[str, Any]]: return []
            async def _close(self): pass # Do nothing for mock
        
        # Create a mock StatusTracker for the backtest engine
        class MockStatusTracker:
            def __init__(self, symbol: str):
                self.status: Dict[str, PairStatus] = {symbol: PairStatus(symbol=symbol)}
        
        mock_status_tracker = MockStatusTracker(self.config.SYMBOL)
        self.position_manager = PositionManager(config, MockExchangeClient(), None, mock_status_tracker) # Pass None for metrics_exporter_obj as it's not used in backtest_engine

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
        print(f"Running backtest for {self.config.SYMBOL} with {len(self.ohlcv)} candles...")

        # Calculate RSI for the entire OHLCV data once
        self.ohlcv['rsi'] = calculate_rsi(self.ohlcv, self.config.RSI_LENGTH)

        for i in range(len(self.ohlcv)):
            current_candle = self.ohlcv.iloc[i]
            current_timestamp_ms = current_candle.name.value // 1_000_000 # Convert nanoseconds to milliseconds
            current_price = current_candle['close']
            
            # Replay liquidation events up to the current candle's timestamp
            await self.cluster_reconstructor.replay_events_up_to_timestamp(current_timestamp_ms)
            
            # Get cluster snapshot
            cluster_snapshot = await self.cluster_aggregator.get_snapshot(current_price)
            is_liquidation_data_available = bool(cluster_snapshot.get("clusters"))

            # Get signal
            # Pass only the relevant portion of the OHLCV for RSI calculation (or just the last few candles)
            # For backtesting, we can pass the whole df, signal_generator will use .iloc[-1]
            trading_signal: Signal = self.signal_generator.decide(
                symbol=self.config.SYMBOL,
                current_price=current_price,
                ohlcv_df=self.ohlcv.iloc[:i+1],
                cluster_snapshot=cluster_snapshot,
                is_liquidation_data_available=is_liquidation_data_available, # Use the actual value

                # --- Incorporate sweep data into signal generation ---
                is_sweep=self.cluster_reconstructor.current_sweep_data.get('is_sweep', False),
                actual_sweep_volume=self.cluster_reconstructor.current_sweep_data.get('actual_sweep_volume', 0.0)
            )

            # --- Trading Logic (using PositionManager) ---
            current_position = self.position_manager.get_open_position(self.config.SYMBOL)

            # Entry
            if trading_signal['signal'] == 1 and not current_position: # Buy signal and no open position
                if self.position_manager.can_open_new_position():
                    amount_usdt = self.config.POSITION_USDT # For simplicity, fixed USDT size
                    amount = amount_usdt / current_price
                    
                    # Calculate SL/TP using PositionManager
                    sl_tp = self.position_manager.calculate_dynamic_sl_tp(current_price, 1, trading_signal.get('top_cluster_price'), trading_signal.get('top_cluster_price'))

                    self.position_manager.update_position(self.config.SYMBOL, {
                        'entry_price': current_price,
                        'amount': amount,
                        'side': 'LONG',
                        'entry_timestamp': current_timestamp_ms,
                        'stop_price': sl_tp['stop_price'],
                        'target_price': sl_tp['target_price']
                    })
                    self._log_backtest_trade(
                        self.config.SYMBOL,
                        datetime.fromtimestamp(current_timestamp_ms / 1000).isoformat(),
                        'LONG', 'Entry', current_price, amount,
                        confidence=trading_signal['confidence'], reason=trading_signal['reason'],
                        target_price=sl_tp['target_price'], stop_price=sl_tp['stop_price'],
                        source=trading_signal['reason']
                    )
                    print(f"Backtest: LONG Entry at {current_price:.2f}. Signal: {trading_signal['reason']}")
                else:
                    print(f"Backtest: Cannot open LONG position. Max open positions reached.")

            # Exit
            elif current_position and (trading_signal['signal'] == -1 or \
                                            (current_position['stop_price'] and current_price <= current_position['stop_price']) or \
                                            (current_position['target_price'] and current_price >= current_position['target_price'])):
                
                pnl = (current_price - current_position['entry_price']) * current_position['amount']
                exit_reason = "RSI_Exit"
                if current_position['stop_price'] and current_price <= current_position['stop_price']:
                    exit_reason = "SL_Hit"
                elif current_position['target_price'] and current_price >= current_position['target_price']:
                    exit_reason = "TP_Hit"

                self._log_backtest_trade(
                    self.config.SYMBOL,
                    datetime.fromtimestamp(current_candle.name.value // 1_000_000 / 1000).isoformat(),
                    'LONG', 'Exit', current_price, current_position['amount'],
                    confidence=trading_signal['confidence'], reason=exit_reason,
                    target_price=current_position['target_price'], stop_price=current_position['stop_price'],
                    pnl=pnl, source=trading_signal['reason']
                )
                print(f"Backtest: LONG Exit at {current_price:.2f}. PnL: {pnl:.2f} USDT. Reason: {exit_reason}")
                self.position_manager.remove_position(self.config.SYMBOL) # Clear position
            
            # Log every signal generated, not just when a trade is executed
            self._log_signal(
                current_timestamp_ms,
                current_price,
                trading_signal
            )

            await asyncio.sleep(0) # Yield control to event loop

        # Save signal log to CSV
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
            'sweep_volume_usdt': signal['sweep_volume_usdt'],
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
            'sweep_volume_usdt': signal['sweep_volume_usdt'],
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
    config.SYMBOL = "SOL/USDT" # Ensure symbol matches dummy data
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