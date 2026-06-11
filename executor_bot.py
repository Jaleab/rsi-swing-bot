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
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
    handlers=[
        logging.StreamHandler(), # Console output
        logging.FileHandler('bot_output.log', mode='a') # File output
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
from src.analysis.signal_quality_tracker import SignalQualityTracker # New import

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
    
    # If mark_price is stale or not set, attempt to fetch a fresh one
    logger.debug(f"[{symbol}] Mark price is stale or not set. Attempting to fetch fresh price.")
    try:
        if Config.SIM_MODE:
            # In SIM_MODE, use a default or a simulated price
            s.mark_price = Config.DEFAULT_SIM_PRICE
            s.last_mark_price_update_ms = current_time_ms
            return s.mark_price
        
        ticker = await exchange_client.exchange.fetch_ticker(symbol)
        mark_price = ticker.get('markPrice')
        if mark_price:
            s.mark_price = float(mark_price)
            s.last_mark_price_update_ms = current_time_ms
            logger.info(f"[{symbol}] Fetched fresh mark price from exchange: {s.mark_price}")
            return s.mark_price
    except Exception as e:
        logger.warning(f"[{symbol}] Could not fetch fresh mark price from exchange: {e}. Using default sim price {Config.DEFAULT_SIM_PRICE}")
        status_tracker.increment_error(symbol) # Increment error count for mark price fetching failure
        # If fetching fails, and we have a stale price, we might still use it or default
        if s.mark_price is not None:
            logger.warning(f"[{symbol}] Using stale mark price: {s.mark_price}")
            return s.mark_price
    
    s.mark_price = Config.DEFAULT_SIM_PRICE # Fallback if no price can be obtained
    s.last_mark_price_update_ms = current_time_ms
    return s.mark_price

async def _update_and_manage_position(
    symbol: str,
    exchange_client: AbstractExchangeClient,
    position_manager: PositionManager,
    status_tracker: StatusTracker,
    signal_generator: SignalGenerator,
    signal_stats_tracker: Dict[str, SignalStatsTracker],
    signal_quality_tracker: Optional[SignalQualityTracker] = None, # New parameter
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
        
        # Check for exit conditions
        exit_signal = signal_generator.check_exit_signal(
            symbol,
            s.mark_price,
            position.position_type,
            position.entry_price,
            position.target_price,
            position.stop_price
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

    # Initialize ohlcv_dataframes outside the loop
    ohlcv_dataframes: Dict[str, pd.DataFrame] = {}

    # Event processing loop
    if sim_events: # Simulation mode
        events_iterator = iter(sim_events)
        # In deterministic simulation, initial OHLCV is fetched by SimEventsGenerator
        # and included in the sim_events list. No need for separate initial fetch here.
    elif event_stream: # Live mode
        # Initial fetch of OHLCV data for all symbols for live mode
        for symbol in Config.SYMBOLS:
            ohlcv_dataframes[symbol] = await fetch_ohlcv(exchange_client, symbol, Config.TIMEFRAME, status_tracker, limit=Config.OHLCV_LIMIT)
            logger.debug(f"[{symbol}] Initial OHLCV data fetched. DataFrame empty: {ohlcv_dataframes[symbol].empty}")
            if ohlcv_dataframes[symbol].empty:
                logger.warning(f"[{symbol}] Initial OHLCV data fetch failed. Signal generation might be affected.")
            else:
                # Calculate RSI for the initial OHLCV data
                ohlcv_dataframes[symbol]['rsi'] = ta.momentum.RSIIndicator(ohlcv_dataframes[symbol]['close'], window=Config.RSI_LENGTH).rsi()
                ohlcv_dataframes[symbol].dropna(inplace=True)
                logger.debug(f"[{symbol}] Initial RSI calculated. DataFrame empty after dropna: {ohlcv_dataframes[symbol].empty}")
                if ohlcv_dataframes[symbol].empty:
                    logger.warning(f"[{symbol}] Initial RSI calculation resulted in empty DataFrame. Signal generation might be affected.")
                logger.debug(f"[{symbol}] Initial OHLCV DataFrame (first 5 rows):\n{ohlcv_dataframes[symbol].head().to_string()}")
        events_iterator = event_stream.get_latest_events()
    else:
        raise ValueError("Either event_stream or sim_events must be provided.")

    # Mark initial data as populated for SimpleMonitor (only if live_tracker is provided)
    if live_tracker:
        live_tracker["initial_data_populated"] = True
        logger.debug("Initial OHLCV data populated for all symbols. Setting live_tracker['initial_data_populated'] = True")

    try: # Outer try for the async for loop
        async for event in events_iterator:
            symbol = event.symbol # Define symbol here so it's available for error logging
            logger.debug(f"Received event in market loop: {type(event).__name__} for {symbol}")
            try: # Inner try for event processing
                s = status_tracker.get_status(symbol)
                current_time_ms = event.timestamp
                
                # Process the event with the cluster aggregator
                await cluster_aggregator.process_event(event)

                # Check if safe mode is active for this symbol
                if s.safe_mode_active:
                    logger.warning(f"[{symbol}] Safe mode is active. Skipping event processing.")
                    # Check if safe mode duration has passed to potentially deactivate
                    if time.time() > s.safe_mode_until:
                        status_tracker.deactivate_safe_mode(symbol)
                    continue

                # Update mark price based on the latest event
                # Use event.timestamp for current_time_ms in simulation for consistency
                if isinstance(event, TradeEvent):
                    s.mark_price = event.price
                    s.last_mark_price_update_ms = current_time_ms
                    logger.debug(f"[{symbol}] Mark price updated from TradeEvent: {s.mark_price}")
                elif isinstance(event, OrderBookEvent):
                    s.mark_price = event.mid_price
                    s.last_mark_price_update_ms = current_time_ms
                    logger.debug(f"[{symbol}] Mark price updated from OrderBookEvent: {s.mark_price}")
                elif isinstance(event, LiquidationEvent):
                    # For liquidation events, we might not want to directly update mark_price
                    # as it represents a momentary price spike during liquidation.
                    # However, for consistency in signal generation, we'll use it if no other price is available.
                    if s.mark_price is None:
                        s.mark_price = event.price
                        s.last_mark_price_update_ms = current_time_ms
                        logger.debug(f"[{symbol}] Mark price updated from LiquidationEvent (fallback): {s.mark_price}")

                # --- Step 1: Update and Manage Existing Positions ---
                # In simulation, this will trigger PaperTrader logic
                await _update_and_manage_position(symbol, exchange_client, position_manager, status_tracker, signal_generators[symbol], signal_stats_trackers[symbol], signal_quality_tracker)

                # --- Step 2: Fetch/Update OHLCV data ---
                # In simulation, OHLCV data is generated or provided deterministically
                latest_ohlcv_df = pd.DataFrame() # Initialize to empty DataFrame
                # In an event-driven loop, fetching OHLCV on every event might be too frequent.
                # We need a strategy to update OHLCV at appropriate intervals or based on candle close events.
                # A more robust solution would would involve a dedicated OHLCV candle builder from raw trades.
                latest_ohlcv_df = await fetch_ohlcv(exchange_client, symbol, Config.TIMEFRAME, status_tracker, limit=Config.OHLCV_LIMIT) # Fetch enough candles for RSI
                if not latest_ohlcv_df.empty:
                    # Append to existing DataFrame and re-calculate RSI if needed
                    # For simplicity, we'll replace the DataFrame for now.
                    ohlcv_dataframes[symbol] = latest_ohlcv_df
                    ohlcv_dataframes[symbol]['rsi'] = ta.momentum.RSIIndicator(ohlcv_dataframes[symbol]['close'], window=Config.RSI_LENGTH).rsi()
                    ohlcv_dataframes[symbol].dropna(inplace=True)
                    logger.debug(f"[{symbol}] OHLCV data updated and RSI recalculated. DataFrame empty after dropna: {ohlcv_dataframes[symbol].empty}")
                    if ohlcv_dataframes[symbol].empty:
                        logger.warning(f"[{symbol}] Initial RSI calculation resulted in empty DataFrame. Signal generation might be affected.")
                
                df = ohlcv_dataframes.get(symbol)
                # Added debug logs for OHLCV DataFrame and current price before signal generation
                logger.debug(f"[{symbol}] OHLCV DataFrame before signal generation (first 5 rows):\n{df.head().to_string() if df is not None else 'None'}")
                logger.debug(f"[{symbol}] Current mark price before signal generation: {s.mark_price}")

                if df is None or df.empty:
                    logger.warning(f"[{symbol}] OHLCV data not available for signal generation. Skipping signal generation.")
                    status_tracker.increment_error(symbol) # Consider this an error if OHLCV is crucial
                    continue

                # --- Step 3: Generate Signal ---
                current_price = s.mark_price
                if current_price is None:
                    logger.warning(f"[{symbol}] Current mark price is None. Skipping signal generation.")
                    status_tracker.increment_error(symbol) # Consider this an error
                    continue

                cluster_snapshot = cluster_aggregator.get_snapshot(symbol)
                is_sweep, actual_sweep_volume = cluster_aggregator.is_sweep_detected(symbol, current_price)
                is_liquidation_data_available = cluster_aggregator.initial_data_ready_event.is_set()

                logger.debug(f"[{symbol}] Calling signal_generator.decide with current_price={current_price}, ohlcv_df_empty={df.empty}, is_liquidation_data_available={is_liquidation_data_available}, is_sweep={is_sweep}")
                signal_data = signal_generators[symbol].decide( # Use specific signal generator
                    symbol=symbol,
                    current_price=current_price,
                    ohlcv_df=df,
                    cluster_snapshot=cluster_snapshot,
                    is_liquidation_data_available=is_liquidation_data_available,
                    is_sweep=is_sweep,
                    actual_sweep_volume=actual_sweep_volume
                )

                signal_type = signal_data.get('signal_type')
                confidence_score = signal_data.get('confidence_score', 0.0)
                
                logger.info(f"[{symbol}] Raw signal_data: {signal_data}")
                if signal_type != "NEUTRAL":
                    logger.info(f"[{symbol}] Signal generated: {signal_type} with confidence {confidence_score:.2f}. Reason: {signal_data.get('reason')}")
                    if metrics_exporter_obj:
                        metrics_exporter_obj.update_last_signal_direction(symbol, 1 if "LONG" in signal_type else (-1 if "SHORT" in signal_type else 0))
                        metrics_exporter_obj.update_last_signal_confidence(symbol, confidence_score)
                        metrics_exporter_obj.update_last_cluster_impact_score(symbol, signal_data.get('cluster_impact_score', 0))
                    status_tracker.update_status(
                        symbol=symbol,
                        last_signal_type=signal_type,
                        last_signal_confidence=confidence_score,
                        last_signal_reason=signal_data.get('reason'),
                        cluster_impact_score=signal_data.get('cluster_impact_score', 0)
                    )
                    
                    # Record signal quality data if in SIM_MODE
                    if Config.SIM_MODE and signal_quality_tracker:
                        signal_quality_tracker.record_signal({
                            "timestamp": current_time_ms,
                            "symbol": symbol,
                            "signal_type": signal_type,
                            "confidence_score": confidence_score,
                            "reason": signal_data.get('reason'),
                            "current_price": current_price,
                            "rsi_value": s.rsi_value, # Assuming rsi_value is stored in status
                            "cluster_impact_score": signal_data.get('cluster_impact_score', 0),
                            "top_cluster_price": s.top_cluster_price, # Assuming this is stored in status
                            "top_cluster_strength": s.top_cluster_strength, # Assuming this is stored in status
                            "imbalance_ratio": s.imbalance_ratio, # Assuming this is stored in status
                            "orderbook_imbalance": s.orderbook_imbalance, # Assuming this is stored in status
                            "trade_imbalance": s.trade_imbalance, # Assuming this is stored in status
                            "sweep_detected": s.sweep_detected # Assuming this is stored in status
                        })
                        logger.debug(f"Recorded signal: {signal_type} for {symbol}")

                    # Update last signal timestamp regardless of whether a position is opened
                    s.last_signal_timestamp = current_time_ms / 1000

                    # --- Guardrail Checks for Signal Processing ---
                    decision_meta = {
                        "decision": "EXECUTED",
                        "block_guard": None,
                        "block_reason": None,
                        "cooldown_active": False,
                        "safe_mode_active": status_tracker.status[symbol].safe_mode_active,
                    }

                    if status_tracker.status[symbol].safe_mode_active:
                        guard_result = GuardResult(allowed=False, reason="Safe mode is active.", guard_name="SAFE_MODE")
                        status_tracker.update_guard_metrics(symbol, guard_result)
                        status_tracker.update_status(symbol, notes=f"SKIPPED_TRADE ({guard_result.guard_name})")
                        decision_meta.update({
                            "decision": "BLOCKED",
                            "block_guard": guard_result.guard_name,
                            "block_reason": guard_result.reason,
                        })
                    elif not position_manager.has_open_position(symbol):
                        # Check for signal cooldown
                        if (current_time_ms / 1000 - s.last_signal_timestamp < Config.SIGNAL_COOLDOWN_SECONDS) and (s.last_signal_type == signal_type):
                            guard_result = GuardResult(
                                allowed=False,
                                reason=f"Signal '{signal_type}' ignored due to cooldown.",
                                guard_name="SIGNAL_COOLDOWN",
                                details=f"Last signal of same type was {current_time_ms / 1000 - s.last_signal_timestamp:.2f}s ago (min {Config.SIGNAL_COOLDOWN_SECONDS}s)."
                            )
                            status_tracker.update_guard_metrics(symbol, guard_result)
                            status_tracker.update_status(symbol, notes=f"SKIPPED_TRADE ({guard_result.guard_name})")
                            decision_meta.update({
                                "decision": "BLOCKED",
                                "block_guard": guard_result.guard_name,
                                "block_reason": guard_result.reason,
                                "cooldown_active": True,
                            })
                        else:
                            # Evaluate guardrails via signal_generator.check_guardrails()
                            guard_results = signal_generators[symbol].check_guardrails(
                                symbol, signal_type, confidence_score,
                                s.mark_price, cluster_snapshot, ohlcv_dataframes.get(symbol, pd.DataFrame())
                            )

                            allowed_by_guardrails = all(gr.allowed for gr in guard_results)

                            for gr in guard_results:
                                status_tracker.update_guard_metrics(symbol, gr)
                                if not gr.allowed:
                                    logger.warning(f"[{symbol}] Trade for signal {signal_type} blocked by guardrail: {gr.guard_name}. Reason: {gr.reason}")
                                    status_tracker.update_status(symbol, notes=f"BLOCKED_TRADE ({gr.guard_name})")
                                    decision_meta.update({
                                        "decision": "BLOCKED",
                                        "block_guard": gr.guard_name,
                                        "block_reason": gr.reason,
                                    })
                                    break

                            if allowed_by_guardrails:
                                logger.info(f"[{symbol}] Signal {signal_type} allowed by all guardrails.")
                                status_tracker.update_status(symbol, notes="TRADE_ALLOWED")

                                # Determine signal direction
                                is_long = 'LONG' in signal_type and signal_type != 'NEUTRAL'
                                is_short = 'SHORT' in signal_type and signal_type != 'NEUTRAL'
                                signal_direction = 'buy' if is_long else 'sell'

                                # Route through PositionManager.open_position() for proper SL/TP + guard enforcement
                                await position_manager.open_position(
                                    symbol=symbol,
                                    signal_direction=signal_direction,
                                    current_price=s.mark_price,
                                    exchange_client=exchange_client,
                                    order_type='market',
                                    signal_stats_tracker=signal_stats_trackers[symbol],
                                    cluster_snapshot=cluster_snapshot
                                )
                    else:
                        logger.info(f"[{symbol}] Already has an open position. Skipping new {signal_type} signal.")
                        status_tracker.update_status(symbol, notes="POSITION_EXISTS")
                        decision_meta.update({
                            "decision": "BLOCKED",
                            "block_guard": "POSITION_EXISTS",
                            "block_reason": "Already has an open position",
                        })

                    # Record signal and decision in SIM_MODE
                    if Config.SIM_MODE and signal_quality_tracker:
                        signal_quality_tracker.record_signal(signal_data, decision_meta)

                # Update live_tracker for SimpleMonitor (if provided)
                if live_tracker:
                    live_tracker[symbol].update({
                        "price": s.mark_price,
                        "rsi": df['rsi'].iloc[-1] if not df.empty and 'rsi' in df.columns else 0.0,
                        "pnl": position.unrealized_pnl if position_manager.has_open_position(symbol) else 0.0,
                        "events": s.event_count,
                        "cluster_vol": cluster_aggregator.get_latest_cluster_volume(symbol),
                        "active_bins": cluster_aggregator.get_active_bin_count(symbol),
                        "status": s.current_status,
                        "notes": s.notes,
                        "signal": s.last_signal_type,
                        "confidence": s.last_signal_confidence,
                        "reason": s.last_signal_reason,
                        "guard_triggered_count": status_tracker.status[symbol].guard_triggered_count, # Corrected from hardcoded 0
                        "guard_trades_blocked_count": status_tracker.status[symbol].guard_trades_blocked_count, # Corrected from hardcoded 0
                        "guard_last_reason": status_tracker.status[symbol].guard_last_reason, # Corrected from hardcoded empty string
                    })

            except asyncio.CancelledError:
                logger.info(f"[{symbol}] Event processing for {symbol} cancelled.")
                raise # Re-raise to be caught by the outer try-finally
            except Exception as e:
                logger.exception(f"[{symbol}] Error processing event for {symbol}: {e}")
                status_tracker.increment_error(symbol)
                status_tracker.update_status(symbol, notes=f"ERROR: {str(e)}")
            
            # Yield control to the event loop to prevent blocking
            await asyncio.sleep(Config.MARKET_LOOP_DELAY)

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
            api_key=Config.BYBIT_API_KEY, 
            api_secret=Config.BYBIT_API_SECRET, 
            testnet=Config.BYBIT_TESTNET,
            # Pass pre-configured symbols to the exchange client
            symbols=Config.DEFAULT_SYMBOLS_RSI_SWING_BOT 
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
            Config.DEFAULT_SYMBOLS_RSI_SWING_BOT,
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
        event_queue = asyncio.Queue()
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
    monitor_thread = Thread(target=monitor.run_monitor)
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
