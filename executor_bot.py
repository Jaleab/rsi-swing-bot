import pandas as pd
import ta
import time
import os
import csv
import asyncio
import logging
import psutil
from datetime import datetime
import sys # Import sys for module inspection
from typing import Dict, Any, Literal, TypedDict, Deque, Optional, List
import requests
import pytz
import numpy as np
import uuid
import inspect
import argparse
import json
from threading import Thread # Added for SimpleMonitor
from src.monitor.simple_monitor import SimpleMonitor # Added for SimpleMonitor
from src.guards import GuardResult # Added for guardrail enforcement

# Configure logging at the very beginning of the file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
    handlers=[
        logging.StreamHandler(), # Console output only
    ]
)
logger = logging.getLogger(__name__)
logger.debug("EXECUTOR_BOT_STARTUP: executor_bot.py script started.")

# --- Rich Console ---
from rich.console import Console
from rich.table import Table
from rich.live import Live

from src.config import Config
from src.metrics_exporter import MetricsExporter
logger.info(f"DEBUG: Config.SIM_MODE at script start: {Config.SIM_MODE}")

# --- Component Imports ---
from src.ws_liquidation import bybit_ws_consumer, binance_ws_consumer, LiquidationEvent
from src.backtest.backtester import Backtester
from src.cluster_aggregator import ClusterAggregator
logger.debug("Importing SignalGenerator...")
from src.signal_generator import SignalGenerator
logger.debug("SignalGenerator imported.")
from src.signal_tracker import SignalStatsTracker
from src.status_tracker import StatusTracker
from src.sim_events_generator import SimEventsGenerator
from src.bybit_exchange import BybitExchangeClient
from src.position_manager import PositionManager
from src.abstract_exchange import AbstractExchangeClient # For type hinting consistency
from src.position import Position
from src.event_stream import EventStream
from src.events import TradeEvent, OrderBookEvent
from src.execution.paper_trader import PaperTrader
from src.analysis.signal_quality_tracker import SignalQualityTracker

# In-memory order tracker (temporary, will be replaced by persistent storage)
_in_memory_order_tracker: Dict[str, Dict[str, Any]] = {}

# Rich table display for live status
async def show_table(status_tracker: StatusTracker):
    """
    Displays a live updating table in the terminal using rich.
    """
    with Live(refresh_per_second=4, screen=True) as live:
        while True:
            display_data = status_tracker.get_display_data()

            table = Table(
                "Symbol", "Last Update", "Price", "Events", "Cluster Vol", "Active Bins",
                "Status", "Notes", "Signal", "Confidence", "Reason",
                title="RSI Swing Bot Live Status"
            )
            
            if display_data:
                for symbol, data in display_data.items():
                    table.add_row(
                        data["Symbol"],
                        data["Last Update"],
                        data["Price"],
                        data["Events"],
                        data["Cluster Vol"],
                        data["Active Bins"],
                        data["Status"],
                        data["Notes"],
                        data["Signal"],
                        data["Confidence"],
                        data["Reason"]
                    )
            else:
                table.add_row(
                    "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "No active pairs", "N/A", "N/A", "N/A"
                )
            
            live.update(table)
            await asyncio.sleep(1)

MAX_RETRIES = 3
RETRY_DELAY = 5 # seconds

# --- Utility Functions ---
def apply_precision(value, step):
    """Applies the precision step to a value."""
    if step == 0:
        return value
    return round(value / step) * step

# Initialize rich console
console = Console()

async def periodic_queue_logger(queue: asyncio.Queue, name: str, interval: int):
    while True:
        await asyncio.sleep(interval)
        logging.info(f"Queue '{name}' size: {queue.qsize()}")
# --- Exchange Setup ---

# --- Data Fetching ---
async def fetch_ohlcv(exchange_client: AbstractExchangeClient, symbol: str, timeframe: str, status_tracker: StatusTracker, limit: int = Config.OHLCV_LIMIT):
    for i in range(MAX_RETRIES):
        try:
            # Use the fetch_ohlcv method from the AbstractExchangeClient
            ohlcv = await exchange_client.fetch_ohlcv(symbol, timeframe, status_tracker, limit=limit)
            if ohlcv is not None and not ohlcv.empty:
                # ohlcv is already a DataFrame from PaperTrader
                logger.debug(f"[{symbol}] Fetched OHLCV data (last {len(ohlcv)} candles) from exchange client.")
                return ohlcv
            else:
                logger.error(f"[{symbol}] Exchange client could not get OHLCV data or returned empty DataFrame.")
                return pd.DataFrame()
        except Exception as e:
            logger.error(f"Error fetching OHLCV with exchange client: {e}. Retrying... ({i+1}/{MAX_RETRIES})")
            await asyncio.sleep(RETRY_DELAY)
    logger.error(f"Failed to fetch OHLCV data after {MAX_RETRIES} retries.")
    return pd.DataFrame()

async def get_account_balance(exchange_client: AbstractExchangeClient) -> float:
    """Fetches the account balance for USDT."""
    for i in range(MAX_RETRIES):
        try:
            balance_info = await exchange_client.fetch_balance()
            usdt_balance = balance_info.get('total', {}).get('USDT', 0.0)
            logger.info(f"Fetched USDT balance: {usdt_balance}")
            return usdt_balance
        except Exception as e:
            logger.error(f"Error fetching account balance: {e}. Retrying... ({i+1}/{MAX_RETRIES})")
            await asyncio.sleep(RETRY_DELAY)
    logger.error(f"Failed to fetch account balance after {MAX_RETRIES} retries.")
    return 0.0

async def get_current_position(exchange_client: AbstractExchangeClient, symbol: str) -> Optional[Dict]:
    """Fetches the current open position for a given symbol."""
    for i in range(MAX_RETRIES):
        try:
            positions = await exchange_client.fetch_positions([symbol])
            # Assuming positions will be a list of dicts, find the one for the symbol
            for position in positions:
                if position['symbol'] == symbol and float(position['size']) != 0:
                    logger.info(f"Fetched current position for {symbol}: {position}")
                    return position
            logger.info(f"No open position found for {symbol}.")
            return None
        except Exception as e:
            logger.error(f"Error fetching current position for {symbol}: {e}. Retrying... ({i+1}/{MAX_RETRIES})")
            await asyncio.sleep(RETRY_DELAY)
    logger.error(f"Failed to fetch current position for {symbol} after {MAX_RETRIES} retries.")
    return None

async def get_symbol_info(exchange_client: AbstractExchangeClient, symbol: str) -> Optional[Dict]:
    """Fetches detailed symbol information."""
    for i in range(MAX_RETRIES):
        try:
            symbol_info = await exchange_client.fetch_symbol_info(symbol)
            logger.info(f"Fetched symbol info for {symbol}: {symbol_info}")
            return symbol_info
        except Exception as e:
            logger.error(f"Error fetching symbol info for {symbol}: {e}. Retrying... ({i+1}/{MAX_RETRIES})")
            await asyncio.sleep(RETRY_DELAY)
    logger.error(f"Failed to fetch symbol info for {symbol} after {MAX_RETRIES} retries.")
    return None


async def _get_current_price(exchange_client: AbstractExchangeClient, symbol: str, status_tracker: StatusTracker):
    s = status_tracker.status[symbol]
    
    current_time_ms = int(time.time() * 1000)

    # Check if existing mark_price is available and not stale
    if s.mark_price is not None and \
       (current_time_ms - s.last_mark_price_update_ms < Config.MAX_LIQUIDATION_DATA_LATENCY_SECONDS * 1000):
        return s.mark_price
    
    logger.debug(f"[{symbol}] Mark price stale or not set. Attempting to fetch fresh price.")
    try:
        if Config.SIM_MODE:
            s.mark_price = Config.DEFAULT_SIM_PRICE
            s.last_mark_price_update_ms = current_time_ms
            return s.mark_price
        
        ticker = await exchange_client.exchange.fetch_ticker(symbol)
        mark_price = ticker.get('markPrice')
        if mark_price:
            s.mark_price = float(mark_price)
            s.last_mark_price_update_ms = current_time_ms
            return s.mark_price
    except Exception as e:
        logger.warning(f"[{symbol}] Ticker fetch failed: {e}")
        if s.mark_price is not None:
            return s.mark_price
    
    s.mark_price = Config.DEFAULT_SIM_PRICE
    s.last_mark_price_update_ms = current_time_ms
    return s.mark_price

async def _update_and_manage_position(
    symbol: str,
    exchange_client: AbstractExchangeClient,
    position_manager: PositionManager,
    status_tracker: StatusTracker,
    signal_generator: SignalGenerator,
    signal_stats_tracker: Dict[str, SignalStatsTracker],
    signal_quality_tracker: Optional[SignalQualityTracker] = None,
    metrics_exporter_obj: Optional[MetricsExporter] = None,
):
    s = status_tracker.status[symbol]
    
    # Update mark price for the current symbol
    current_mark_price = await _get_current_price(exchange_client, symbol, status_tracker)
    s.mark_price = current_mark_price
    logger.debug(f"[{symbol}] Updated mark price: {s.mark_price}")

    # Get the current position for the symbol
    position = position_manager.get_open_position(symbol)

    if position:
        logger.debug(f"[{symbol}] Position found: {position}")
        # Update position details and check for exit conditions
        position.update_unrealized_pnl(s.mark_price)
        position.update_current_value(s.mark_price)
        
        logger.debug(f"[{symbol}] Unrealized PnL: {position.unrealized_pnl}, Current Value: {position.current_value}")
        logger.debug(f"[{symbol}] Current Mark Price: {s.mark_price:.2f}, Entry Price: {position.entry_price:.2f}, Target Price: {position.target_price if position.target_price is not None else 'None'}, Stop Price: {position.stop_price if position.stop_price is not None else 'None'}")

        # Update Prometheus metrics for open positions
        metrics_exporter_obj.update_open_positions_count(position_manager.get_total_open_positions())
        metrics_exporter_obj.update_unrealized_pnl(symbol, position.unrealized_pnl)
        metrics_exporter_obj.update_realized_pnl(symbol, position.realized_pnl)
        metrics_exporter_obj.update_position_entry(symbol, position.entry_price)
        metrics_exporter_obj.update_target_price(symbol, position.target_price)
        metrics_exporter_obj.update_stop_price(symbol, position.stop_price)
        metrics_exporter_obj.update_current_value_usdt(symbol, position.current_value)
        metrics_exporter_obj.update_mark_price(symbol, s.mark_price)
        
        # Check for exit conditions (SL, TP, and RSI reversal)
        exit_signal = signal_generator.check_exit_signal(
            symbol,
            s.mark_price,
            position.position_type,
            position.entry_price,
            position.target_price,
            position.stop_price,
            rsi_value=s.rsi_value
        )

        if exit_signal:
            logger.info(f"[{symbol}] Exit signal detected: {exit_signal}. Closing position.")
            # Use the update function for cumulative PnL
            # Update cumulative PnL with the realized PnL from the closed position
            metrics_exporter_obj.update_cumulative_pnl(position.realized_pnl)
            
            # Close the position using the position object's method
            position.close_position(s.mark_price, datetime.now().timestamp() * 1000)
            logger.info(f"[{symbol}] Position closed. Realized PnL: {position.realized_pnl}")
            
            # If in SIM_MODE, record the trade result
            if Config.SIM_MODE and signal_quality_tracker:
                trade_result_data = {
                    "symbol": symbol,
                    "entry_price": position.entry_price,
                    "exit_price": s.mark_price,
                    "position_type": position.position_type,
                    "size": position.size,
                    "realized_pnl": position.realized_pnl,
                    "timestamp": datetime.now().timestamp() * 1000,
                    "exit_reason": exit_signal, # Use the exit signal as the reason
                }
                trade_result_data.update({
                    "signal_timestamp": position.entry_timestamp, # Assuming position object stores this
                    "order_id": position.order_id # Assuming position object stores this
                })
                signal_quality_tracker.record_trade_result(trade_result_data)
                logger.debug(f"Recorded trade result for {symbol}: PnL={position.realized_pnl}")

            # Remove the position from the manager's tracking
            position_manager.remove_position(symbol)
            
            metrics_exporter_obj.update_open_positions_count(position_manager.get_total_open_positions())
            
            # Clear position-specific metrics
            metrics_exporter_obj.update_unrealized_pnl(symbol, 0)
            metrics_exporter_obj.update_realized_pnl(symbol, 0)
            metrics_exporter_obj.update_position_entry(symbol, 0)
            metrics_exporter_obj.update_target_price(symbol, 0)
            metrics_exporter_obj.update_stop_price(symbol, 0)
            metrics_exporter_obj.update_current_value_usdt(symbol, 0)
            metrics_exporter_obj.update_mark_price(symbol, 0)
    else:
        logger.debug(f"[{symbol}] No open position to manage.")

async def _process_single_event(
    event, symbol, exchange_client, position_manager, status_tracker,
    cluster_aggregator, signal_generators, signal_stats_trackers,
    signal_quality_tracker, metrics_exporter_obj, ohlcv_dataframes,
    last_ohlcv_update, live_tracker,
):
    """Process a single event (or timer tick) for one symbol."""
    try:
        s = status_tracker.status.get(symbol)
        if not s:
            return
        if event is not None:
            await cluster_aggregator.process_event(event)
        if s.safe_mode_active:
            if time.time() > s.safe_mode_until:
                status_tracker.deactivate_safe_mode(symbol)
            return
        await _update_and_manage_position(symbol, exchange_client, position_manager, status_tracker,
            signal_generators.get(symbol, signal_generators[list(signal_generators.keys())[0]]),
            signal_stats_trackers.get(symbol, signal_stats_trackers[list(signal_stats_trackers.keys())[0]]),
            signal_quality_tracker, metrics_exporter_obj)
        current_time = time.time()
        if (symbol not in last_ohlcv_update or
            (current_time - last_ohlcv_update.get(symbol, 0)) >= Config.OHLCV_UPDATE_INTERVAL_S):
            ohlcv_df = await fetch_ohlcv(exchange_client, symbol, Config.TIMEFRAME, status_tracker, limit=Config.OHLCV_LIMIT)
            if not ohlcv_df.empty:
                ohlcv_dataframes[symbol] = ohlcv_df
                ohlcv_dataframes[symbol]['rsi'] = ta.momentum.RSIIndicator(ohlcv_dataframes[symbol]['close'], window=Config.RSI_LENGTH).rsi()
                ohlcv_dataframes[symbol].dropna(inplace=True)
            last_ohlcv_update[symbol] = current_time
        df = ohlcv_dataframes.get(symbol)
        if df is None or df.empty:
            return
        if s.mark_price is None or s.mark_price == Config.DEFAULT_SIM_PRICE:
            s.mark_price = float(df['close'].iloc[-1])
            s.last_mark_price_update_ms = int(time.time() * 1000)
        current_price = s.mark_price
        if current_price is None:
            return
        cluster_snapshot = cluster_aggregator.get_snapshot(symbol)
        is_sweep, bullish, bearish = cluster_aggregator.is_sweep_detected(symbol, current_price)
        is_liq_avail = cluster_aggregator.initial_data_ready_event.is_set()
        if metrics_exporter_obj:
            vol = sum(c.get('volume', 0) for c in cluster_snapshot.get('clusters', []))
            metrics_exporter_obj.update_cluster_volume(symbol, vol)
            metrics_exporter_obj.update_active_bins(symbol, len(cluster_snapshot.get('clusters', [])))
        signal_data = signal_generators[symbol].decide(
            symbol=symbol, current_price=current_price, ohlcv_df=df,
            cluster_snapshot=cluster_snapshot, is_liquidation_data_available=is_liq_avail,
            is_sweep=is_sweep, bullish_sweep_volume=bullish, bearish_sweep_volume=bearish,
        )
        signal_type = signal_data.get('signal_type', 'NEUTRAL')
        confidence = signal_data.get('confidence_score', 0.0)
        if signal_type != "NEUTRAL":
            logger.info(f"[{symbol}] Signal: {signal_type} confidence={confidence:.3f}")
            if metrics_exporter_obj:
                metrics_exporter_obj.update_last_signal_direction(symbol, 1 if 'LONG' in signal_type else -1)
                metrics_exporter_obj.update_last_signal_confidence(symbol, confidence)
            s.last_signal_timestamp = time.time()
            status_tracker.update_status(symbol, last_signal_type=signal_type, last_signal_confidence=confidence,
                                         last_signal_reason=signal_data.get('reason', ''))
            if not position_manager.has_open_position(symbol):
                guard_results = signal_generators[symbol].check_guardrails(
                    symbol, signal_type, confidence, current_price, cluster_snapshot, df)
                if all(gr.allowed for gr in guard_results):
                    is_long = 'LONG' in signal_type
                    await position_manager.open_position(
                        symbol=symbol, signal_direction='buy' if is_long else 'sell',
                        current_price=s.mark_price, exchange_client=exchange_client,
                        order_type='market', signal_stats_tracker=signal_stats_trackers[symbol],
                        cluster_snapshot=cluster_snapshot,
                    )
        if live_tracker:
            live_tracker[symbol].update({
                "price": s.mark_price,
                "rsi": df['rsi'].iloc[-1] if not df.empty and 'rsi' in df.columns else 0.0,
                "pnl": position_manager.get_open_position(symbol).unrealized_pnl if position_manager.has_open_position(symbol) else 0.0,
                "events": s.events_count,
                "status": s.status,
                "signal": s.last_signal_type,
                "confidence": s.last_signal_confidence,
                "reason": s.last_signal_reason,
            })
    except Exception as e:
        logger.error(f"[{symbol}] _process_single_event error: {e}", exc_info=True)
        status_tracker.increment_error(symbol)


async def market_loop(
    exchange_client: AbstractExchangeClient,
    position_manager: PositionManager,
    status_tracker: StatusTracker,
    cluster_aggregator: ClusterAggregator,
    signal_generators: Dict[str, SignalGenerator],
    signal_stats_trackers: Dict[str, SignalStatsTracker],
    event_stream: Optional[EventStream] = None,
    sim_events: Optional[List[Any]] = None,
    signal_quality_tracker: Optional[SignalQualityTracker] = None, # New parameter
    live_tracker: Optional[Dict[str, Dict[str, Any]]] = None, # Added live_tracker
    metrics_exporter_obj: Optional[MetricsExporter] = None, # New parameter
):
    """
    Main loop for the trading bot, processing events and generating signals.
    Can operate in live mode with an event_stream or simulation mode with pre-generated sim_events.
    """
    logger.info("Market loop started.")
    print("DEBUG: Market loop started (print statement).") # Added print

    # Initialize ohlcv_dataframes and throttling outside the loop
    ohlcv_dataframes: Dict[str, pd.DataFrame] = {}
    last_ohlcv_update: Dict[str, float] = {}

    # Initial OHLCV fetch for live mode
    if event_stream:  # Live mode
        for symbol in Config.SYMBOLS:
            ohlcv_dataframes[symbol] = await fetch_ohlcv(exchange_client, symbol, Config.TIMEFRAME, status_tracker, limit=Config.OHLCV_LIMIT)
            if not ohlcv_dataframes[symbol].empty:
                ohlcv_dataframes[symbol]['rsi'] = ta.momentum.RSIIndicator(ohlcv_dataframes[symbol]['close'], window=Config.RSI_LENGTH).rsi()
                ohlcv_dataframes[symbol].dropna(inplace=True)
            last_ohlcv_update[symbol] = time.time()

    # Mark initial data as populated for SimpleMonitor
    if live_tracker:
        live_tracker["initial_data_populated"] = True

    # Market loop: event-driven for sim, timer-driven for live
    try:
        if sim_events:
            for event in sim_events:
                symbol = event.symbol
                await _process_single_event(event, symbol, exchange_client, position_manager,
                    status_tracker, cluster_aggregator, signal_generators, signal_stats_trackers,
                    signal_quality_tracker, metrics_exporter_obj, ohlcv_dataframes, last_ohlcv_update, live_tracker)
                await asyncio.sleep(Config.MARKET_LOOP_DELAY)
        elif event_stream:
            logger.info("Market loop entering timer-driven mode (5s interval)...")
            while True:
                for symbol in Config.SYMBOLS:
                    s = status_tracker.status.get(symbol)
                    if not s:
                        continue
                    mid = cluster_aggregator.get_orderbook_mid_price(symbol)
                    if mid and mid > 0:
                        s.mark_price = mid
                        s.last_mark_price_update_ms = int(time.time() * 1000)
                    await _process_single_event(None, symbol, exchange_client, position_manager,
                        status_tracker, cluster_aggregator, signal_generators, signal_stats_trackers,
                        signal_quality_tracker, metrics_exporter_obj, ohlcv_dataframes, last_ohlcv_update, live_tracker)
                await asyncio.sleep(Config.MARKET_LOOP_INTERVAL)
        else:
            raise ValueError("Either event_stream or sim_events must be provided.")
    
    except asyncio.CancelledError:
        logger.info("Market loop cancelled.")
    except Exception as e:
        logger.exception(f"Unhandled exception in market loop: {e}")
    finally:
        await exchange_client.close()
        # Flush signal quality data if in SIM_MODE
        if Config.SIM_MODE and signal_quality_tracker:
            print("DEBUG: Calling signal_quality_tracker.flush_to_disk()")
            signal_quality_tracker.flush_to_disk()
        
        # Cancel all running tasks besides the current one
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Bot stopped.")


async def main():
    console.print(f"[bold green]Starting RSI Swing Bot in {'SIMULATION' if Config.SIM_MODE else 'LIVE'} Mode...[/bold green]")
    
    # Initialize live_tracker here
    live_tracker: Dict[str, Any] = {
        "initial_data_populated": False
    }

    # Add initial status entries for each symbol
    for symbol in Config.SYMBOLS:
        live_tracker[symbol] = {
            "price": 0.0,
            "rsi": 0.0,
            "pnl": 0.0,
            "volume": 0.0,
            "events": 0,
            "active_bins": 0,
            "status": "Initializing",
            "notes": "",
            "signal": "NEUTRAL",
            "confidence": 0.0,
            "reason": "",
            "guard_triggered_count": 0,
            "guard_trades_blocked_count": 0,
            "guard_last_reason": "",
        }

    # 1. Initialize StatusTracker
    status_tracker = StatusTracker(Config.SYMBOLS, live_tracker)
    # 2. Initialize MetricsExporter and start its server
    metrics_exporter_instance = MetricsExporter()
    logger.info(f"MetricsExporter initialized.")
    metrics_update_thread = Thread(target=metrics_exporter_instance.start_metrics_server, args=(Config.PROMETHEUS_PORT,))
    metrics_update_thread.daemon = True
    metrics_update_thread.start()
    metrics_update_task = None # No asyncio task for this, it's a thread


    # 3. Initialize Argument Parser
    parser = argparse.ArgumentParser(description='RSI Swing Bot')
    parser.add_argument('--sim', action='store_true', help='Run in simulation mode')
    args = parser.parse_args()
    Config.SIM_MODE = args.sim
    print(f"DEBUG: Config.SIM_MODE after argparse: {Config.SIM_MODE}")
    logger.info(f"DEBUG: Config.SIM_MODE after argparse: {Config.SIM_MODE}")


    # 4. Initialize SignalQualityTracker (only in SIM_MODE)
    signal_quality_tracker: Optional[SignalQualityTracker] = None
    if Config.SIM_MODE:
        signal_quality_tracker = SignalQualityTracker()
        logger.debug(f"SignalQualityTracker initialized with output_dir: {signal_quality_tracker.output_dir}")
        logger.debug(f"SignalQualityTracker initialized with output_dir: {signal_quality_tracker.output_dir}")
        print("DEBUG: SignalQualityTracker initialized in SIM_MODE.")
    
    # 5. Initialize exchange client
    exchange_client: AbstractExchangeClient
    if Config.SIM_MODE:
        print(f"DEBUG: PaperTrader loaded from module: {PaperTrader.__module__}, file: {sys.modules[PaperTrader.__module__].__file__}")
        print(f"DEBUG: PaperTrader __init__ source: {inspect.getsource(PaperTrader.__init__)}")
        exchange_client = PaperTrader(
            config=Config,
            initial_balance=Config.SIMULATION_INITIAL_BALANCE
        )
    else:
        exchange_client = BybitExchangeClient(
            api_key=Config.API_KEY, 
            api_secret=Config.API_SECRET, 
            testnet=Config.TESTNET,
            metrics_exporter=metrics_exporter_instance
        )

    # 6. Initialize PositionManager instances and populate the dictionary
    position_managers = {symbol: PositionManager(config=Config, exchange_client=exchange_client, metrics_exporter_obj=metrics_exporter_instance, status_tracker=status_tracker) for symbol in Config.SYMBOLS}
    
    # 7. Assign the populated position_managers to status_tracker
    status_tracker.position_managers = position_managers

    # For consistency, main_position_manager will be the first one in the list (arbitrary for now)
    position_manager = next(iter(position_managers.values()))
    
    # 8. Initialize ClusterAggregator
    cluster_aggregator: ClusterAggregator
    if Config.SIM_MODE:
        cluster_aggregator = ClusterAggregator(Config, status_tracker) # No event_queue in sim mode
    else:
        event_queue: asyncio.Queue = asyncio.Queue() # Create event_queue for live mode
        cluster_aggregator = ClusterAggregator(Config, status_tracker, event_queue) # With event_queue in live mode

    # 9. Initialize signal generator and stats tracker for each symbol
    signal_generators = {symbol: SignalGenerator(Config, cluster_aggregator) for symbol in Config.SYMBOLS}
    signal_stats_trackers = {symbol: SignalStatsTracker(symbol) for symbol in Config.SYMBOLS}

    # Initialize SimEventsGenerator if in simulation mode
    sim_events_generator = None
    all_generated_events = [] # List to store all events generated by SimEventsGenerator
    if Config.SIM_MODE:
        sim_events_generator = SimEventsGenerator(
            Config, # Pass the Config object
            Config.SYMBOLS,
            Config.TIMEFRAME,
            Config.OHLCV_LIMIT,
            exchange_client, # Pass PaperTrader as exchange client for sim events
            status_tracker,
            random_seed=Config.SIMULATION_RANDOM_SEED
        )
        logger.info("Calling sim_events_generator.generate_events()...")
        sim_events = await sim_events_generator.generate_all_events(Config.SIM_DURATION_SECONDS)
        all_generated_events = sim_events # Assign directly, as generate_events already returns the list
        logger.info(f"Generated {len(all_generated_events)} events for simulation.")
    
    # 9. Initialize EventStream (only in live mode)
    event_stream: Optional[EventStream] = None
    if not Config.SIM_MODE:
        event_stream = EventStream(
            event_queue,
            cluster_aggregator,
            Config,
            status_tracker
        )
        logger.info("Calling event_stream.start()...")
        await event_stream.start() # Start the event stream and its consumers
        logger.info("EventStream started.")

    # Start the SimpleMonitor in a separate thread
    monitor = SimpleMonitor(status_tracker, position_manager, cluster_aggregator, live_tracker)
    monitor_thread = Thread(target=monitor.run)
    monitor_thread.daemon = True
    monitor_thread.start()


    # Start a background task for updating resource metrics if not in simulation mode
    metrics_update_task = None
    if not Config.SIM_MODE:
        metrics_update_task = asyncio.create_task(metrics_exporter_instance.update_resource_metrics_loop(Config.SYMBOLS, status_tracker))

    # Run the market loop
    print("DEBUG: Attempting to run market loop...")
    try:
        if Config.SIM_MODE:
            await market_loop(
                exchange_client,
                position_managers[Config.SYMBOLS[0]], # Pass the first position manager, as market_loop iterates through symbols
                status_tracker,
                cluster_aggregator,
                signal_generators, # Pass the dictionary of signal generators
                signal_stats_trackers, # Pass the dictionary of signal stats trackers
                sim_events=all_generated_events, # Pass pre-generated events for simulation
                signal_quality_tracker=signal_quality_tracker, # Pass tracker for SIM-only recording
                live_tracker=live_tracker, # Pass live_tracker to market_loop
                metrics_exporter_obj=metrics_exporter_instance,
            )
        else: # Live mode
            await market_loop(
                exchange_client,
                position_managers[Config.SYMBOLS[0]], # Pass the first position manager, as market_loop iterates through symbols
                status_tracker,
                cluster_aggregator,
                signal_generators,
                signal_stats_trackers,
                event_stream=event_stream, # Pass the event stream for live mode
                live_tracker=live_tracker, # Pass live_tracker to market_loop
                metrics_exporter_obj=metrics_exporter_instance,
            )
    except asyncio.CancelledError:
        logger.info("Market loop cancelled.")
    except Exception as e:
        logger.exception(f"Unhandled exception in market loop: {e}")
    finally:
        await exchange_client.close()
        # Flush signal quality data if in SIM_MODE
        if Config.SIM_MODE and signal_quality_tracker:
            print("DEBUG: Calling signal_quality_tracker.flush_to_disk()")
            signal_quality_tracker.flush_to_disk()
        
        # Stop the metrics update loop if it's running
        if metrics_update_task:
            metrics_update_task.cancel()
            try:
                await metrics_update_task
            except asyncio.CancelledError:
                logger.info("Metrics update loop cancelled.")
        
        # Cancel all running tasks besides the current one
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Bot stopped.")


if __name__ == "__main__":
    # The 'events' variable is now passed from main to market_loop
    # and its type depends on SIM_MODE or live mode.
    # The `main()` function is now cleaner and delegates event handling to market_loop.
    asyncio.run(main())
