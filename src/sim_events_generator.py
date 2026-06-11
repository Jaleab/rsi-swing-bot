import asyncio
import json
import time
import logging
import random
from collections import deque, defaultdict
from typing import Deque, Dict, Any, Optional, Literal, List

from src.config import Config
from src.ws_liquidation import LiquidationEvent
from src.status_tracker import StatusTracker
from src.event_stream import OrderBookEvent, TradeEvent

logger = logging.getLogger(__name__)

class SimEventsGenerator:
    """
    Generates synthetic liquidation events or replays historical events
    to feed into the ClusterAggregator for testing and development.
    """
    def __init__(self, config: Config, symbols: List[str], timeframe: str, ohlcv_limit: int, exchange_client: Any, status_tracker: StatusTracker, random_seed: int):
        self.config = config
        self.symbols = symbols
        self.timeframe = timeframe
        self.ohlcv_limit = ohlcv_limit
        self.exchange_client = exchange_client
        self.status_tracker = status_tracker
        self.all_generated_events: List[Any] = [] # Store all generated events
        self.random_generator = random.Random(random_seed) # Initialize with seed
        logger.info(f"SimEventsGenerator initialized with seed: {random_seed}")
        logger.debug(f"SimEventsGenerator initialized with symbols: {self.symbols}")

        self.base_prices = {
            "BTC/USDT": 30000.0,
            "ETH/USDT": 2000.0,
            "SOL/USDT": 230.0,
            "DOGE/USDT": 0.25,
            "XRP/USDT": 0.5,
            "ASTER/USDT": 0.05,
            "FARTCOIN/USDT": 0.0001,
            "XPL/USDT": 0.1,
            "AIA/USDT": 0.00000001,
            "BNB/USDT": 300.0
        }
        for symbol in self.symbols:
            initial_price = self.base_prices.get(symbol, 1.0)
            self.status_tracker.update_status(symbol=symbol, mark_price=initial_price)
            logger.info(f"[{symbol}] Initialized StatusTracker mark_price to {initial_price} during SimEventsGenerator init.")

    async def load_historical_events(self, file_path: str):
        """Loads historical liquidation events from a JSON file."""
        # This method is kept for potential future use with historical event replay,
        # but for deterministic simulation, events are generated synthetically.
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                for event_data in data:
                    event = LiquidationEvent(
                        exchange=event_data.get("exchange", "bybit"),
                        symbol=event_data.get("symbol", "UNKNOWN/USDT"),
                        timestamp=event_data.get("timestamp", int(time.time() * 1000)),
                        price=float(event_data.get("price", 0.0)),
                        qty=float(event_data.get("qty", 0.0)),
                        qty_usdt=float(event_data.get("qty_usdt", 0.0)),
                        side=event_data.get("side", "LONG"),
                        order_id=event_data.get("order_id", "")
                    )
                    self.all_generated_events.append(event) # Append to the main event list
            logger.info(f"Loaded {len(self.all_generated_events)} historical events from {file_path}.")
        except FileNotFoundError:
            logger.error(f"Historical events file not found: {file_path}")
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding historical events JSON from {file_path}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error loading historical events: {e}")

    # Commenting out replay_events as it's not used in the deterministic simulation
    # async def replay_events(self, speed_factor: float = 1.0):
    #     """
    #     Replays loaded historical events into the event queue.
    #     speed_factor: 1.0 for real-time, >1.0 for faster, <1.0 for slower.
    #     """
    #     if not self.events:
    #         logger.warning("No events loaded to replay.")
    #         return

    #     logger.info(f"Starting replay of {len(self.events)} events with speed factor {speed_factor}...")
        
    #     self.events = deque(sorted(self.events, key=lambda x: x.timestamp))

    #     start_time_real = time.monotonic()
    #     start_time_sim = self.events[0].timestamp

        while self.events:
            event = self.events.popleft()
            
            sim_elapsed = (event.timestamp - start_time_sim) / 1000.0
            real_elapsed_target = sim_elapsed / speed_factor
            wait_time = real_elapsed_target - (time.monotonic() - start_time_real)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            
            await self.event_queue.put(event)
            logger.debug(f"Replayed event: {event.symbol} @ {event.price} ({event.side})")
        
        logger.info("Finished replaying historical events.")

    async def _generate_single_event(self, symbol: str, timestamp: int):
        """Generates a single synthetic liquidation event for a given symbol."""
        logger.debug(f"[{symbol}] _generate_single_event: Generating single liquidation event.")
        current_mark_price = self.status_tracker.status[symbol].mark_price if self.status_tracker.status[symbol].mark_price else self.base_prices.get(symbol, 1.0)

        price = current_mark_price * (1 + (self.random_generator.random() - 0.5) * 0.01)
        volume_usdt = self.random_generator.uniform(5000.0, 20000.0)
        side = self.random_generator.choice(["LONG", "SHORT"])
        
        event = LiquidationEvent(
            exchange="bybit",
            symbol=symbol,
            timestamp=timestamp, # Use passed timestamp
            price=price,
            qty=volume_usdt / price,
            qty_usdt=volume_usdt,
            side=side,
            order_id=f"synthetic_single_event_{symbol}_{timestamp}"
        )
        self.all_generated_events.append(event)
        self.status_tracker.update_status(symbol=symbol, mark_price=price)
        logger.debug(f"[{symbol}] Single event generated and mark price updated to {price}: {event}")

    # Commenting out inject_manual_sweep as it's not used in the deterministic simulation
    # async def inject_manual_sweep(self):
    #     """
    #     Injects a single synthetic sweep event based on Config parameters.
    #     """
    #     symbol = self.config.MANUAL_SWEEP_SYMBOL
    #     volume_usdt = self.config.MANUAL_SWEEP_VOLUME_USDT
    #     direction = self.config.MANUAL_SWEEP_DIRECTION.upper()
    #     price_impact_pct = self.config.MANUAL_SWEEP_PRICE_IMPACT_PCT

    #     if symbol not in self.config.SYMBOLS:
    #         logger.error(f"Manual sweep symbol {symbol} is not in configured symbols. Skipping manual sweep injection.")
    #         return

    #     current_mark_price = self.status_tracker.status[symbol].mark_price
    #     if current_mark_price is None:
    #         logger.error(f"Cannot inject manual sweep for {symbol}: current mark price is not available. Skipping.")
    #         return

    #     price_change_factor = 1 + (price_impact_pct / 100.0)
    #     if direction == "SHORT":
    #         price_change_factor = 1 - (price_impact_pct / 100.0)
        
    #     target_price = current_mark_price * price_change_factor
        
    #     logger.info(f"Injecting manual {direction} sweep for {symbol} with {volume_usdt} USDT volume, targeting price {target_price:.2f} (impact: {price_impact_pct}%).")

    #     await self.generate_synthetic_sweep(
    #         symbol=symbol,
    #         price=target_price,
    #         volume_usdt=volume_usdt,
    #         side="LONG" if direction == "BUY" else "SHORT",
    #         num_events=self.config.SIM_SWEEP_NUM_EVENTS,
    #         duration_s=self.config.SIM_SWEEP_DURATION_S
    #     )
    #     logger.info(f"Manual sweep injection for {symbol} complete.")

    async def generate_synthetic_sweep(self,
                                       symbol: str,
                                       price: float,
                                       volume_usdt: float,
                                       side: Literal["LONG", "SHORT"],
                                       num_events: int = 5,
                                       duration_s: float = 1.0,
                                       current_simulation_time_ms: int = 0): # Add current_simulation_time_ms
        """
        Generates a synthetic sweep event (burst of liquidations) at a specific price.
        """
        logger.debug(f"[generate_synthetic_sweep] Generating sweep for symbol: {symbol}")
        logger.info(f"Generating synthetic {side} sweep for {symbol} at {price} with {volume_usdt} USDT volume across {num_events} events over {duration_s}s.")
        
        base_timestamp = current_simulation_time_ms # Use simulation time
        volume_per_event = volume_usdt / num_events
        # No delay_per_event needed as we are generating all events upfront
        
        for i in range(num_events):
            try:
                qty = volume_per_event / price
            except ZeroDivisionError:
                logger.error(f"[{symbol}] ZeroDivisionError: price is zero ({price}). Cannot calculate qty. Skipping event.")
                continue
            
            # Distribute events over the duration_s
            event_timestamp = base_timestamp + int((i / num_events) * duration_s * 1000) + self.random_generator.randint(-50, 50)
            event_price = price * (1 + (self.random_generator.random() - 0.5) * 0.001)
            
            event = LiquidationEvent(
                exchange="bybit",
                symbol=symbol,
                timestamp=event_timestamp,
                price=event_price,
                qty=qty,
                qty_usdt=volume_per_event,
                side=side,
                order_id=f"synthetic_sweep_{base_timestamp}_{i}"
            )
            self.all_generated_events.append(event) # Append to the main event list
            self.status_tracker.update_status(symbol=symbol, mark_price=event_price)
            logger.debug(f"[{event.symbol}] Event generated and mark price updated to {event.price}: {event}")
        
        logger.info(f"Finished generating synthetic {side} sweep for {symbol}.")

    async def _generate_initial_historical_events(self, symbol: str, current_simulation_time_ms: int):
        """Generates a series of synthetic liquidation events to populate historical data."""
        logger.info(f"[{symbol}] _generate_initial_historical_events: Generating initial historical events for {self.config.HISTORICAL_WINDOW_S} seconds...")
        
        # Use current_simulation_time_ms as the "current" time for historical generation
        start_time_ms = current_simulation_time_ms - self.config.HISTORICAL_WINDOW_S * 1000
        
        num_events_per_second = 0.5
        total_events_to_generate = int(num_events_per_second * self.config.HISTORICAL_WINDOW_S)
        
        for i in range(total_events_to_generate):
            # Distribute historical events within the window
            timestamp = self.random_generator.randint(start_time_ms, current_simulation_time_ms)
            
            current_mark_price = self.status_tracker.status[symbol].mark_price if self.status_tracker.status[symbol].mark_price else self.base_prices.get(symbol, 1.0)

            price = current_mark_price * (1 + (self.random_generator.random() - 0.5) * 0.01)
            volume_usdt = self.random_generator.uniform(500.0, 5000.0)
            side = self.random_generator.choice(["LONG", "SHORT"])
            
            event = LiquidationEvent(
                exchange="bybit",
                symbol=symbol,
                timestamp=timestamp,
                price=price,
                qty=volume_usdt / price,
                qty_usdt=volume_usdt,
                side=side,
                order_id=f"synthetic_historical_event_{symbol}_{timestamp}_{i}"
            )
            self.all_generated_events.append(event) # Append to the main event list
            self.status_tracker.update_status(symbol=symbol, mark_price=price)
        logger.info(f"[{symbol}] Finished generating {total_events_to_generate} initial historical events and updated mark prices.")


    async def _generate_synthetic_order_book_update(self, symbol: str, timestamp: int):
        """Generates a synthetic order book update event."""
        current_price = self.status_tracker.status[symbol].mark_price or self.base_prices.get(symbol, 1.0)
        bids = [[current_price * (1 - self.random_generator.uniform(0.0001, 0.001)), round(self.random_generator.uniform(0.1, 5.0), 3)] for _ in range(5)]
        asks = [[current_price * (1 + self.random_generator.uniform(0.0001, 0.001)), round(self.random_generator.uniform(0.1, 5.0), 3)] for _ in range(5)]
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        event = OrderBookEvent(
            exchange="simulated",
            symbol=symbol,
            timestamp=timestamp, # Use passed timestamp
            mid_price=(bids[0][0] + asks[0][0]) / 2 if bids and asks else current_price,
            imbalance=round(self.random_generator.uniform(0.3, 0.7), 2)
        )
        self.all_generated_events.append(event)
        self.status_tracker.update_status(symbol=symbol, mark_price=event.mid_price)
        logger.info(f"[{symbol}] Synthetic OrderBookEvent generated and mark price updated to {event.mid_price}: {event}")

    async def _generate_synthetic_trade_event(self, symbol: str, timestamp: int):
        """Generates a synthetic trade event."""
        current_price = self.status_tracker.status[symbol].mark_price or self.base_prices.get(symbol, 1.0)
        price = current_price * (1 + (self.random_generator.random() - 0.5) * 0.0002)
        amount = round(self.random_generator.uniform(0.001, 0.1), 4)
        side = self.random_generator.choice(['LONG', 'SHORT'])

        event = TradeEvent(
            exchange="simulated",
            symbol=symbol,
            timestamp=timestamp, # Use passed timestamp
            price=price,
            qty=amount,
            side=side,
            trade_id=f"synthetic_trade_{timestamp}_{symbol}",
            imbalance=round(self.random_generator.uniform(0.8, 1.2), 2)
        )
        self.all_generated_events.append(event)
        self.status_tracker.update_status(symbol=symbol, mark_price=price)
        logger.info(f"[{symbol}] Synthetic TradeEvent generated and mark price updated to {price}: {event}")

    async def generate_all_events(self, simulation_duration_s: int):
        """
        Generates all synthetic events for the entire simulation duration upfront.
        """
        logger.info(f"Generating all events for simulation duration: {simulation_duration_s} seconds...")
        
        current_simulation_time_ms = int(time.time() * 1000) # Starting simulation time

        print(f"DEBUG: Initializing event generation. current_simulation_time_ms: {current_simulation_time_ms}")

        # Generate initial historical data for all symbols
        logger.info("Generating initial historical data for all symbols...")
        for symbol in self.config.SYMBOLS:
            await self._generate_initial_historical_events(symbol, current_simulation_time_ms)
        logger.info("Finished generating initial historical data for all symbols.")

        # Generate initial order book and trade events for all symbols
        logger.info("Generating initial order book and trade events for all symbols...")
        for symbol in self.config.SYMBOLS:
            await self._generate_synthetic_order_book_update(symbol, current_simulation_time_ms)
            await self._generate_synthetic_trade_event(symbol, current_simulation_time_ms)
        logger.info("Finished generating initial order book and trade events for all symbols.")

        if self.config.ENABLE_MANUAL_SWEEP_INJECTION:
            logger.info("Manual sweep injection enabled. Generating a single manual sweep.")
            # Manual sweep will be generated at the current simulation time
            await self._inject_manual_sweep_deterministic(current_simulation_time_ms)
            logger.info("Manual sweep injected. No further events will be generated for manual injection mode.")
        else:
            logger.info("Starting continuous synthetic event generation (liquidations, order book, trades).")

            # Simulate events over the entire duration
            for t in range(simulation_duration_s):
                simulation_time_ms = current_simulation_time_ms + t * 1000 # Advance simulation time by 1 second

                for symbol in self.config.SYMBOLS:
                    # Generate a single liquidation event
                    await self._generate_single_event(symbol, simulation_time_ms)

                    # Generate a sweep sometimes
                    if self.random_generator.random() < self.config.SIM_SWEEP_FREQUENCY:
                        await self._generate_single_sweep_for_symbol(symbol, simulation_time_ms)

                    # Generate order book and trade events
                    await self._generate_synthetic_order_book_update(symbol, simulation_time_ms)
                    await self._generate_synthetic_trade_event(symbol, simulation_time_ms)
        
        logger.info(f"Sorting {len(self.all_generated_events)} generated events by timestamp...")
        self.all_generated_events.sort(key=lambda event: event.timestamp)
        logger.info("All events generated and sorted.")
        print(f"DEBUG: Total events generated: {len(self.all_generated_events)}")
        return self.all_generated_events

    async def _generate_single_sweep_for_symbol(self, symbol: str, current_simulation_time_ms: int):
        logger.debug(f"[_generate_single_sweep_for_symbol] Processing symbol: {symbol}")
        """Helper to generate a single sweep for a given symbol."""
        logger.info(f"Simulating sweep for {symbol}...")

        current_mark_price = self.status_tracker.status[symbol].mark_price if self.status_tracker.status[symbol].mark_price else self.base_prices.get(symbol, 1.0)

        price = current_mark_price * (1 + (self.random_generator.random() - 0.5) * 0.02)
        volume_usdt = self.random_generator.uniform(200000.0, 1000000.0)
        side = self.random_generator.choice(["LONG", "SHORT"])
        num_events = self.random_generator.randint(20, 50)
        duration_s = self.random_generator.uniform(1.0, 5.0)

        await self.generate_synthetic_sweep(
            symbol=symbol,
            price=price,
            volume_usdt=volume_usdt,
            side=side,
            num_events=num_events,
            duration_s=duration_s,
            current_simulation_time_ms=current_simulation_time_ms # Pass current simulation time
        )


