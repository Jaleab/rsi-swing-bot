import pandas as pd
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import numpy as np # Import numpy

from src.config import Config
from src.abstract_exchange import AbstractExchangeClient
from src.bybit_exchange import BybitExchangeClient
from src.rsi_calc import calculate_rsi
from src.cluster_aggregator import ClusterAggregator
from src.signal_generator import SignalGenerator
from src.position_manager import PositionManager
from src.status_tracker import StatusTracker
from src.metrics_exporter import MetricsExporter # Import the MetricsExporter class
from src.event_stream import EventStream
from src.events import SimulatedLiquidationEvent # Import from new events module

logger = logging.getLogger(__name__)

class Backtester:
    def __init__(self, config: Config, exchange: AbstractExchangeClient, symbols: List[str], metrics_exporter_obj: MetricsExporter):
        self.config = config
        self.exchange = exchange
        self.symbols = symbols
        self.metrics_exporter = metrics_exporter_obj # Store the MetricsExporter instance
        self.np_random_generator = np.random.default_rng(self.config.SIMULATION_RANDOM_SEED) # Initialize with seed
        self.ohlcv_data: Dict[str, pd.DataFrame] = {}
        self.liquidation_data: Dict[str, List[SimulatedLiquidationEvent]] = {}
        self.cluster_aggregators: Dict[str, ClusterAggregator] = {}
        self.signal_generators: Dict[str, SignalGenerator] = {}
        self.position_managers: Dict[str, PositionManager] = {}
        self.status_tracker = StatusTracker(symbols, self.position_managers)
        self.event_stream = EventStream()

        self.current_timestamp: Optional[datetime] = None
        self.backtest_start_date: Optional[datetime] = None
        self.backtest_end_date: Optional[datetime] = None
        self.initial_balance = self.config.BACKTEST_INITIAL_BALANCE
        self.balance = self.initial_balance
        self.trades: List[Dict[str, Any]] = []

        for symbol in self.symbols:
            # Pass the full config object, not just the symbol
            self.cluster_aggregators[symbol] = ClusterAggregator(config, self.status_tracker)
            self.signal_generators[symbol] = SignalGenerator(config, self.cluster_aggregators[symbol]) # Corrected instantiation
            self.position_managers[symbol] = PositionManager(config, self.exchange, self.metrics_exporter, self.status_tracker)

    async def _load_historical_data(self, start_date: datetime, end_date: datetime):
        logger.info(f"Loading historical data from {start_date} to {end_date}...")
        for symbol in self.symbols:
            logger.info(f"Generating dummy OHLCV for {symbol} for backtesting...")
            ohlcv_data = []
            current_time = start_date
            while current_time <= end_date:
                # Generate some price variation around a base price
                base_price = 20000.0 if "BTC" in symbol else 1500.0 # Example base prices
                open_p = base_price * (1 + (self.np_random_generator.uniform(-0.001, 0.001)))
                close_p = open_p * (1 + (self.np_random_generator.uniform(-0.001, 0.001)))
                high_p = max(open_p, close_p) * (1 + self.np_random_generator.uniform(0, 0.0005))
                low_p = min(open_p, close_p) * (1 - self.np_random_generator.uniform(0, 0.0005))
                volume = 100 + (self.np_random_generator.uniform(0, 50))

                ohlcv_data.append([int(current_time.timestamp() * 1000), open_p, high_p, low_p, close_p, volume])
                current_time += timedelta(minutes=1)

            df = pd.DataFrame(ohlcv_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            self.ohlcv_data[symbol] = df
            logger.info(f"Generated {len(df)} dummy OHLCV candles for {symbol}.")

            self.liquidation_data[symbol] = [] # TODO: Implement loading/generation of historical liquidations

        logger.info("Historical data loading complete.")

    async def run_backtest(self, start_date: datetime, end_date: datetime):
        self.backtest_start_date = start_date
        self.backtest_end_date = end_date
        self.current_timestamp = start_date

        await self._load_historical_data(start_date, end_date)

        # Main backtesting loop
        while self.current_timestamp <= self.backtest_end_date:
            mark_price = None # Initialize mark_price
            for symbol in self.symbols:
                # Process OHLCV data
                if symbol in self.ohlcv_data and not self.ohlcv_data[symbol].empty:
                    try:
                        current_ohlcv = self.ohlcv_data[symbol].loc[self.current_timestamp]
                        mark_price = current_ohlcv['close']
                        self.status_tracker.update_status(symbol=symbol, mark_price=mark_price) # Corrected method call

                        # Calculate RSI
                        # Need enough data for RSI calculation, usually RSI_LENGTH periods
                        historical_closes = self.ohlcv_data[symbol].loc[:self.current_timestamp]['close'].tail(self.config.RSI_LENGTH + 1)
                        if len(historical_closes) > self.config.RSI_LENGTH:
                            # Convert Series to DataFrame with a 'close' column for calculate_rsi
                            historical_closes_df = pd.DataFrame({'close': historical_closes})
                            rsi_series = calculate_rsi(historical_closes_df, length=self.config.RSI_LENGTH)
                            rsi_value = rsi_series.iloc[-1] # Get the last (most recent) RSI value as a scalar
                            self.metrics_exporter.update_rsi(symbol, rsi_value)
                            self.status_tracker.update_status(symbol=symbol, rsi_value=rsi_value) # Corrected method call
                        else:
                            rsi_value = 50.0 # Default neutral RSI if not enough data
                            self.status_tracker.update_status(symbol=symbol, rsi_value=rsi_value) # Corrected method call
                    except KeyError:
                        logger.debug(f"[{symbol}] No OHLCV data for {self.current_timestamp}. Skipping.")
                        continue # Skip this timestamp if no OHLCV data

                if mark_price is None:
                    # If no OHLCV data for current timestamp, use previous mark price or default
                    # For backtesting, we should ideally have continuous data.
                    # If there's a gap, we might need to forward-fill or skip.
                    # For now, if mark_price is None, we skip signal generation and position management for this timestamp.
                    logger.debug(f"[{symbol}] Mark price not available for {self.current_timestamp}. Skipping signal generation and position management.")
                    continue

                # TODO: Process liquidation events for the current_timestamp
                # For now, we'll simulate an empty stream
                # liquidations_for_interval = [liq for liq in self.liquidation_data[symbol] if liq.timestamp == self.current_timestamp]
                # self.cluster_aggregators[symbol].add_liquidations(liquidations_for_interval)

                # In backtesting, we might ingest simulated liquidation events here,
                # or just let the cluster aggregator's internal timer handle expiration.
                # For now, we'll just let the internal expiration logic run.
                # If no new events are ingested, then _expire_old_events will still be called
                # when ingest is called or manually.
                # For backtesting, we manually call _expire_old_events to update clusters based on time.
                self.cluster_aggregators[symbol]._expire_old_events(symbol)

                # Get cluster data after potential expiration
                cluster_data = self.cluster_aggregators[symbol].get_snapshot(symbol) # Use get_snapshot for cluster data
                
                # Update metrics with data from the snapshot
                self.metrics_exporter.update_cluster_volume(symbol, sum(c["volume"] for c in cluster_data["clusters"]))
                self.metrics_exporter.update_active_bins(symbol, len(cluster_data["clusters"]))


                # Generate signal
                signal = self.signal_generators[symbol].decide(
                    symbol=symbol,
                    current_price=mark_price,
                    ohlcv_df=self.ohlcv_data[symbol].loc[:self.current_timestamp].tail(self.config.RSI_LENGTH + 1), # Pass relevant OHLCV data
                    cluster_snapshot=cluster_data,
                    sweep_volume=(False, 0.0), # No real-time sweeps in backtest yet
                    is_liquidation_data_available=True # Assume available for backtest
                )
                self.metrics_exporter.update_last_signal_direction(symbol, 1 if "LONG" in signal.get('signal_type', "") else (-1 if "SHORT" in signal.get('signal_type', "") else 0)) # Convert signal_type to int
                self.metrics_exporter.update_last_signal_confidence(symbol, signal.get('confidence_score', 0.0)) # Use .get with default
                self.metrics_exporter.update_last_cluster_impact_score(symbol, signal.get('cluster_impact_score', 0)) # Use .get with default

                # Manage position
                async with self.status_tracker.position_lock:
                    position_info = self.status_tracker.get_position_info(symbol)
                    current_price = mark_price # Use mark price for backtesting

                    if not position_info.is_open:
                        if signal.get('signal_type') != "NEUTRAL": # If there's a new signal
                            # Calculate desired position size based on initial balance and risk
                            risk_amount = self.balance * self.config.RISK_PER_TRADE_PERCENT
                            entry_price = current_price
                            # Assuming signal.get('direction') returns a string like "LONG" or "SHORT"
                            signal_direction_int = 1 if "LONG" in signal.get('signal_type', "") else (-1 if "SHORT" in signal.get('signal_type', "") else 0)
                            stop_loss_price = self.position_managers[symbol].calculate_stop_loss(entry_price, signal_direction_int)
                            
                            if stop_loss_price is not None and entry_price is not None:
                                price_diff = abs(entry_price - stop_loss_price)
                                if price_diff > 0:
                                    # Crude estimate of position size in base currency
                                    # This assumes 1x leverage and a simple calculation
                                    # In a real scenario, this would involve more detailed margin calculations
                                    size_base_currency = risk_amount / price_diff
                                    size_usdt = size_base_currency * entry_price # Convert to USDT for consistency

                                    # Ensure position size meets minimum and doesn't exceed available balance
                                    if size_usdt > self.config.MIN_POSITION_SIZE_USDT and size_usdt * current_price <= self.balance:
                                        # Simulate opening a position
                                        self.position_managers[symbol].open_position(
                                            signal_direction_int, entry_price, size_usdt,
                                            signal.get('take_profit_price'), stop_loss_price
                                        )
                                        self.balance -= size_usdt * current_price # Deduct from balance (simplified)
                                        logger.info(f"[{self.current_timestamp}] SIMULATED TRADE: Opened {signal_direction_int} position for {symbol} at {entry_price} with size {size_usdt:.2f} USDT. SL: {stop_loss_price}, TP: {signal.get('take_profit_price')}")
                                        self.trades.append({
                                            'timestamp': self.current_timestamp,
                                            'symbol': symbol,
                                            'type': 'open',
                                            'direction': signal_direction_int,
                                            'entry_price': entry_price,
                                            'size_usdt': size_usdt,
                                            'stop_loss': stop_loss_price,
                                            'take_profit': signal.get('take_profit_price'),
                                            'pnl': 0.0
                                        })
                                        self.metrics_exporter.increment_trades_total(symbol, 'backtest', 'open')
                                        self.metrics_exporter.update_open_positions_count(len([s for s in self.symbols if self.status_tracker.get_position_info(s).is_open]))
                                    else:
                                        logger.debug(f"[{self.current_timestamp}] SIMULATED TRADE: Not opening position for {symbol} due to size constraints or insufficient balance. Desired size: {size_usdt:.2f} USDT, Balance: {self.balance:.2f}")
                                else:
                                    logger.debug(f"[{self.current_timestamp}] SIMULATED TRADE: Price difference for SL is zero. Cannot open position for {symbol}.")
                            else:
                                logger.debug(f"[{self.current_timestamp}] SIMULATED TRADE: SL or Entry Price is None. Cannot open position for {symbol}.")
                    else: # Position is open, check for SL/TP or close signal
                        pnl = self.position_managers[symbol].check_pnl(current_price)
                        self.metrics_exporter.update_unrealized_pnl(symbol, pnl)

                        closed_trade = self.position_managers[symbol].check_close_conditions(current_price, signal_direction_int if 'signal_type' in signal else 0)
                        if closed_trade:
                            self.balance += closed_trade['exit_price'] * closed_trade['size_usdt'] + closed_trade['pnl'] # Add PnL to balance
                            logger.info(f"[{self.current_timestamp}] SIMULATED TRADE: Closed {closed_trade['direction']} position for {symbol} at {closed_trade['exit_price']} with PnL {closed_trade['pnl']:.2f} USDT. Reason: {closed_trade['close_reason']}")
                            self.trades.append({
                                'timestamp': self.current_timestamp,
                                'symbol': symbol,
                                'type': 'close',
                                'direction': closed_trade['direction'],
                                'entry_price': closed_trade['entry_price'],
                                'exit_price': closed_trade['exit_price'],
                                'size_usdt': closed_trade['size_usdt'],
                                'pnl': closed_trade['pnl'],
                                'close_reason': closed_trade['close_reason']
                            })
                            self.metrics_exporter.increment_trades_total(symbol, 'backtest', 'close')
                            self.metrics_exporter.update_trade_profit(symbol, closed_trade['pnl'], 'backtest', closed_trade['close_reason'])
                            self.metrics_exporter.update_realized_pnl(symbol, closed_trade['pnl'])
                            self.metrics_exporter.update_cumulative_pnl(self.balance - self.initial_balance)
                            self.metrics_exporter.update_open_positions_count(len([s for s in self.symbols if self.status_tracker.get_position_info(s).is_open]))
                            
                # Update status tracker metrics
                position_info = self.status_tracker.get_position_info(symbol)
                self.metrics_exporter.update_open_position(symbol, position_info.is_open)
                self.metrics_exporter.update_position_size_usdt(symbol, position_info.size_usdt)
                self.metrics_exporter.update_position_entry(symbol, position_info.entry_price)
                self.metrics_exporter.update_target_price(symbol, position_info.take_profit_price)
                self.metrics_exporter.update_stop_price(symbol, position_info.stop_loss_price)
                self.metrics_exporter.update_current_value_usdt(symbol, position_info.current_value_usdt)

            self.current_timestamp += timedelta(minutes=1) # Move to the next minute
            if self.current_timestamp.minute % 10 == 0: # Log progress every 10 minutes
                logger.info(f"Backtest progress: {self.current_timestamp} / {self.backtest_end_date}. Current Balance: {self.balance:.2f} USDT")

        logger.info("Backtesting complete.")
        self._generate_backtest_report()

    def _generate_backtest_report(self):
        logger.info("\n--- Backtest Report ---")
        logger.info(f"Initial Balance: {self.initial_balance:.2f} USDT")
        logger.info(f"Final Balance: {self.balance:.2f} USDT")
        total_pnl = self.balance - self.initial_balance
        logger.info(f"Total PnL: {total_pnl:.2f} USDT")
        logger.info(f"Total Trades: {len(self.trades)}")

        if self.trades:
            trades_df = pd.DataFrame(self.trades)
            closed_trades = trades_df[trades_df['type'] == 'close']
            if not closed_trades.empty:
                total_profit = closed_trades['pnl'].sum()
                winning_trades = closed_trades[closed_trades['pnl'] > 0]
                losing_trades = closed_trades[closed_trades['pnl'] < 0]

                win_rate = len(winning_trades) / len(closed_trades) if len(closed_trades) > 0 else 0
                avg_win = winning_trades['pnl'].mean() if not winning_trades.empty else 0
                avg_loss = losing_trades['pnl'].mean() if not losing_trades.empty else 0
                profit_factor = abs(winning_trades['pnl'].sum() / losing_trades['pnl'].sum()) if not losing_trades.empty else float('inf')

                logger.info(f"Total Closed PnL: {total_profit:.2f} USDT")
                logger.info(f"Winning Trades: {len(winning_trades)}")
                logger.info(f"Losing Trades: {len(losing_trades)}")
                logger.info(f"Win Rate: {win_rate:.2%}")
                logger.info(f"Average Win: {avg_win:.2f} USDT")
                logger.info(f"Average Loss: {avg_loss:.2f} USDT")
                logger.info(f"Profit Factor: {profit_factor:.2f}")

                # Calculate drawdown (simplified)
                equity_curve = []
                current_equity = self.initial_balance
                for _, row in trades_df.iterrows():
                    if row['type'] == 'close':
                        current_equity += row['pnl']
                    equity_curve.append(current_equity)
                
                equity_series = pd.Series(equity_curve)
                peak = equity_series.expanding(min_periods=1).max()
                drawdown = (equity_series - peak) / peak
                max_drawdown = drawdown.min() * -1 if not drawdown.empty else 0

                logger.info(f"Maximum Drawdown: {max_drawdown:.2%}")
            else:
                logger.info("No closed trades to report on.")
        else:
            logger.info("No trades were executed during the backtest.")

        # Optionally save trades to a CSV
        # trades_df.to_csv("backtest_trades.csv", index=False)
        # logger.info("Backtest trades saved to backtest_trades.csv")

async def main():
    # Example usage:
    # Set up a temporary config for backtesting if needed, or use the main Config
    config = Config()
    config.SIM_MODE = True # Ensure simulation mode is active for backtesting
    config.USE_SIM_EVENTS_GENERATOR = False # We'll load historical data directly

    # Initialize MetricsExporter for backtesting
    metrics_exporter_instance = MetricsExporter()

    # For backtesting, you might want to use a mock exchange or a real one
    # that can fetch historical data. BybitExchange can fetch historical OHLCV.
    exchange = BybitExchange(config)
    
    # Define symbols for backtesting
    symbols = ["BTC/USDT", "ETH/USDT"]

    backtester = Backtester(config, exchange, symbols, metrics_exporter_instance)

    # Define your backtest period
    start_date = datetime(2023, 1, 1, 0, 0, 0)
    end_date = datetime(2023, 1, 3, 23, 59, 0)

    await backtester.run_backtest(start_date, end_date)

if __name__ == "__main__":
    logging.basicConfig(level=Config.LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(message)s')
    asyncio.run(main())