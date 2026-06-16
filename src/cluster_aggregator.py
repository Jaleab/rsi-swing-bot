import asyncio
import time
from collections import deque, defaultdict
from typing import Dict, Deque, List, Optional, Tuple, Any
import json
import os
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG) # Set logger level to DEBUG for more verbose output

# Assuming LiquidationEvent is imported from ws_liquidation
from .ws_liquidation import LiquidationEvent
from .events import TradeEvent, OrderBookEvent # Import from new events module
from .config import Config

class ClusterAggregator:
    def __init__(self, config: Config, status_tracker, event_queue: Optional[asyncio.Queue] = None):
        self.config = config
        self.status_tracker = status_tracker
        self.event_queue = event_queue # Unified event queue (optional, used in live mode)
        self.bins: Dict[str, Dict[int, Dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: {"volume": 0.0, "bullish_volume": 0.0, "bearish_volume": 0.0, "last_update": 0, "events": 0}))
        self.events_deque: Dict[str, Deque[LiquidationEvent]] = defaultdict(lambda: deque())
        self.last_save_time = time.time()

        # Event to signal that initial data has been processed for all symbols from all relevant queues
        self.initial_data_ready_event = asyncio.Event()
        self._initial_data_first_received: float = 0.0  # timestamp when first event was received
        # Initialized based on Config.SYMBOLS
        self.initial_data_received_for_symbols = {symbol: {'liquidation': False, 'orderbook': False, 'trade': False} for symbol in self.config.SYMBOLS}

        # New attributes for trade and order book data
        self.trade_imbalances: Dict[str, float] = defaultdict(lambda: 1.0) # Default to balanced
        self.orderbook_mid_prices: Dict[str, float] = defaultdict(lambda: 0.0)
        self.orderbook_imbalances: Dict[str, float] = defaultdict(lambda: 1.0)

        if self.config.PERSIST_CLUSTERS:
            self._load_state()

    def _get_bin_size(self, symbol: str, price: float) -> float:
        if self.config.BIN_MODE == "percent":
            return price * self.config.BIN_PCT
        return self.config.BIN_ABS

    def _price_to_bin(self, symbol: str, price: float) -> int:
        bin_size = self._get_bin_size(symbol, price)
        return int(price // bin_size)

    def ingest(self, event: Any):
        if isinstance(event, LiquidationEvent):
            self._ingest_liquidation_event(event)
        elif isinstance(event, TradeEvent):
            self._ingest_trade_event(event)
        elif isinstance(event, OrderBookEvent):
            self._ingest_orderbook_event(event)
        else:
            logger.warning(f"Unknown event type received by ClusterAggregator: {type(event)}")

    def _ingest_liquidation_event(self, event: LiquidationEvent):
        symbol = event.symbol
        bin_idx = self._price_to_bin(symbol, event.price)

        self.events_deque[symbol].append(event)

        self.bins[symbol][bin_idx]["volume"] += event.qty_usdt
        self.bins[symbol][bin_idx]["last_update"] = event.timestamp
        self.bins[symbol][bin_idx]["events"] += 1
        if event.side == "LONG":       # Longs liquidated = forced selling = bearish
            self.bins[symbol][bin_idx]["bearish_volume"] += event.qty_usdt
        elif event.side == "SHORT":    # Shorts liquidated = forced buying = bullish
            self.bins[symbol][bin_idx]["bullish_volume"] += event.qty_usdt

        self._expire_old_events(symbol, current_time_ms_override=event.timestamp) # Pass event timestamp

        self.status_tracker.update_status(
            symbol=symbol,
            last_update_ms=event.timestamp,
            events_count=len(self.events_deque[symbol]),
            cluster_volume_usdt=sum(b["volume"] for b in self.bins[symbol].values()),
            active_bins=len(self.bins[symbol])
        )

    def _ingest_trade_event(self, event: TradeEvent):
        self.trade_imbalances[event.symbol] = event.imbalance
        logger.debug(f"[{event.symbol}] Trade imbalance updated: {self.trade_imbalances[event.symbol]}")

    def _ingest_orderbook_event(self, event: OrderBookEvent):
        self.orderbook_mid_prices[event.symbol] = event.mid_price
        self.orderbook_imbalances[event.symbol] = event.imbalance
        logger.debug(f"[{event.symbol}] Orderbook mid-price updated: {self.orderbook_mid_prices[event.symbol]}, imbalance: {self.orderbook_imbalances[event.symbol]}")

    def get_trade_imbalance(self, symbol: str) -> float:
        return self.trade_imbalances[symbol]

    def get_orderbook_mid_price(self, symbol: str) -> float:
        return self.orderbook_mid_prices[symbol]

    def get_orderbook_imbalance(self, symbol: str) -> float:
        return self.orderbook_imbalances[symbol]

    def _expire_old_events(self, symbol: str, current_time_ms_override: Optional[int] = None):
        current_time_ms = current_time_ms_override if current_time_ms_override is not None else int(time.time() * 1000)
        
        while self.events_deque[symbol] and \
              self.events_deque[symbol][0].timestamp < (current_time_ms - self.config.SLIDING_WINDOW_S * 1000):
            
            old_event = self.events_deque[symbol].popleft()
            old_bin_idx = self._price_to_bin(symbol, old_event.price)

            if old_bin_idx in self.bins[symbol]:
                self.bins[symbol][old_bin_idx]["volume"] -= old_event.qty_usdt
                self.bins[symbol][old_bin_idx]["events"] -= 1
                if old_event.side == "LONG":
                    self.bins[symbol][old_bin_idx]["bearish_volume"] -= old_event.qty_usdt
                elif old_event.side == "SHORT":
                    self.bins[symbol][old_bin_idx]["bullish_volume"] -= old_event.qty_usdt

                if self.bins[symbol][old_bin_idx]["volume"] <= 0 or self.bins[symbol][old_bin_idx]["events"] <= 0:
                    del self.bins[symbol][old_bin_idx]
        
        if not self.bins[symbol]:
            self.bins.pop(symbol, None)
        if not self.events_deque[symbol]:
            self.events_deque.pop(symbol, None)


    def get_bin_strength_at(self, symbol: str, price: float) -> Tuple[float, float]:
        """
        Returns normalized strength (volume) and percentile relative to recent bin strengths.
        Normalized strength is volume / median_volume.
        """
        bin_idx = self._price_to_bin(symbol, price)
        bin_volume = self.bins[symbol].get(bin_idx, {}).get("volume", 0.0)

        all_volumes = [b["volume"] for b in self.bins[symbol].values() if b["volume"] > 0]
        if not all_volumes:
            return 0.0, 0.0

        median_volume = sorted(all_volumes)[len(all_volumes) // 2]
        if median_volume == 0:
            normalized_strength = 0.0
        else:
            normalized_strength = bin_volume / median_volume

        if bin_volume == 0:
            percentile = 0.0
        else:
            less_than_count = sum(1 for v in all_volumes if v < bin_volume)
            equal_to_count = sum(1 for v in all_volumes if v == bin_volume)
            percentile = (less_than_count + 0.5 * equal_to_count) / len(all_volumes) * 100
        
        logger.debug(f"[{symbol}] Bin strength at price {price}: normalized_strength={normalized_strength}, percentile={percentile}")
        return normalized_strength, percentile

    def get_top_n_clusters(self, symbol: str, n: int, side: Optional[str] = None) -> List[Dict]:
        """
        Returns list of top N clusters with volume and centroid price.
        Clusters are sorted by volume in descending order.
        """
        active_bins = []
        for bin_idx, data in self.bins[symbol].items():
            approx_price = (bin_idx + 0.5) * self._get_bin_size(symbol, self._get_approx_current_price(symbol))
            
            active_bins.append({
                "bin_idx": bin_idx,
                "volume": data["volume"],
                "centroid_price": approx_price,
                "last_update": data["last_update"],
                "events_count": data["events"]
            })
        
        active_bins.sort(key=lambda x: x["volume"], reverse=True)
        return active_bins[:n]

    def _get_approx_current_price(self, symbol: str) -> float:
        """
        Retrieves the current mark price for a symbol from the status_tracker.
        This is used for dynamic bin sizing.
        """
        status = self.status_tracker.status.get(symbol)
        if status and status.mark_price is not None:
            logger.debug(f"[{symbol}] _get_approx_current_price using status_tracker mark_price: {status.mark_price}")
            return status.mark_price
        
        # Fallback to averaging recent event prices if mark_price is not available
        if self.events_deque[symbol]:
            recent_prices = [e.price for e in list(self.events_deque[symbol])[-10:]]
            if recent_prices:
                avg_price = sum(recent_prices) / len(recent_prices)
                logger.debug(f"[{symbol}] _get_approx_current_price falling back to average of recent events: {avg_price}")
                return avg_price
        
        logger.debug(f"[{symbol}] _get_approx_current_price returning default 1.0 (no mark_price or events)")
        return 1.0

    def is_sweep_detected(self, symbol: str, current_price: float, current_time_ms_override: Optional[int] = None) -> Tuple[bool, float, float]:
        """
        Checks if a liquidation sweep is detected at the current price.
        Returns (is_sweep, bullish_volume, bearish_volume) — directional sweep volumes.
        """
        bin_idx = self._price_to_bin(symbol, current_price)
        
        current_time_ms = current_time_ms_override if current_time_ms_override is not None else int(time.time() * 1000)
        sweep_window_events = [
            event for event in self.events_deque[symbol]
            if event.timestamp >= (current_time_ms - self.config.SWEEP_WINDOW_S * 1000)
            and self._price_to_bin(symbol, event.price) == bin_idx
        ]
        
        bullish_volume = sum(e.qty_usdt for e in sweep_window_events if e.side == "SHORT")
        bearish_volume = sum(e.qty_usdt for e in sweep_window_events if e.side == "LONG")
        recent_volume = bullish_volume + bearish_volume

        historical_events = [
            event for event in self.events_deque[symbol]
            if event.timestamp < (current_time_ms - self.config.SWEEP_WINDOW_S * 1000)
            and event.timestamp >= (current_time_ms - self.config.SLIDING_WINDOW_S * 1000)
            and self._price_to_bin(symbol, event.price) == bin_idx
        ]
        historical_volume_in_bin = sum(event.qty_usdt for event in historical_events)
        historical_event_count_in_bin = len(historical_events)

        historical_avg = historical_volume_in_bin / historical_event_count_in_bin if historical_event_count_in_bin > 0 else 0.0

        if historical_avg == 0 and recent_volume > 0:
            is_sweep = recent_volume >= self.config.MIN_SWEEP_VOLUME_USDT
        else:
            is_sweep = recent_volume >= max(self.config.MIN_SWEEP_VOLUME_USDT, historical_avg * self.config.SWEEP_THRESHOLD_FACTOR)
        
        bin_size = self._get_bin_size(symbol, current_price)
        bin_start_price = bin_idx * bin_size
        bin_end_price = (bin_idx + 1) * bin_size
        price_touch_condition = bin_start_price <= current_price < bin_end_price

        logger.debug(f"[{symbol}] Sweep detection: bullish={bullish_volume:.2f}, bearish={bearish_volume:.2f}, is_sweep={is_sweep}")
        return is_sweep and price_touch_condition, bullish_volume, bearish_volume

    def get_snapshot(self, symbol: str, current_time_ms_override: Optional[int] = None) -> Dict:
        """
        Returns a snapshot of the cluster aggregator state for a given symbol.
        """
        current_time_ms = current_time_ms_override if current_time_ms_override is not None else int(time.time() * 1000)
        
        # Ensure old events are expired before taking snapshot to reflect accurate state
        self._expire_old_events(symbol, current_time_ms_override=current_time_ms)

        clusters = []
        for bin_idx, data in self.bins[symbol].items():
            approx_price = (bin_idx + 0.5) * self._get_bin_size(symbol, self._get_approx_current_price(symbol))
            clusters.append({
                "bin_idx": bin_idx,
                "volume": data["volume"],
                "bullish_volume": data.get("bullish_volume", 0.0),
                "bearish_volume": data.get("bearish_volume", 0.0),
                "centroid_price": approx_price,
                "last_update": data["last_update"],
                "events_count": data["events"]
            })
        
        all_volumes = [c["volume"] for c in clusters if c["volume"] > 0]
        median_volume = sorted(all_volumes)[len(all_volumes) // 2] if all_volumes else 0.0

        top_clusters = self.get_top_n_clusters(symbol, n=5)

        return {
            "clusters": clusters,
            "top_clusters": top_clusters,
            "median_volume": median_volume,
            "timestamp": int(time.time() * 1000)
        }

    def _get_state_filepath(self) -> str:
        os.makedirs(self.config.DATA_DIR, exist_ok=True)
        return os.path.join(self.config.DATA_DIR, self.config.CLUSTER_STATE_FILE)

    def _load_state(self):
        filepath = self._get_state_filepath()
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f:
                    state_data = json.load(f)
                    
                    self.bins = defaultdict(lambda: defaultdict(lambda: {"volume": 0.0, "bullish_volume": 0.0, "bearish_volume": 0.0, "last_update": 0, "events": 0}),
                                            {s: defaultdict(lambda: {"volume": 0.0, "bullish_volume": 0.0, "bearish_volume": 0.0, "last_update": 0, "events": 0}, b)
                                             for s, b in state_data.get("bins", {}).items()})
                    # Migrate old state without directional volumes
                    for symbol in self.bins:
                        for bin_idx in self.bins[symbol]:
                            bin_data = self.bins[symbol][bin_idx]
                            if "bullish_volume" not in bin_data:
                                bin_data["bullish_volume"] = 0.0
                            if "bearish_volume" not in bin_data:
                                bin_data["bearish_volume"] = 0.0
                    
                    for symbol, events_list in state_data.get("events_deque", {}).items():
                        self.events_deque[symbol] = deque(
                            [LiquidationEvent(
                                exchange=e['exchange'],
                                symbol=e['symbol'],
                                timestamp=e['timestamp'],
                                price=e['price'],
                                qty=e['qty'],
                                qty_usdt=e['qty_usdt'],
                                side=e['side'],
                                order_id=e.get('order_id')
                            ) for e in events_list]
                        )
                logger.info(f"ClusterAggregator state loaded from {filepath}")
            except Exception as e:
                logger.error(f"Error loading ClusterAggregator state: {e}")
        else:
            logger.info(f"No existing ClusterAggregator state file found at {filepath}")

    def _save_state(self):
        filepath = self._get_state_filepath()
        try:
            serializable_events_deque = {
                symbol: [event.__dict__ for event in dq]
                for symbol, dq in self.events_deque.items()
            }
            
            state_data = {
                "bins": {s: dict(b) for s, b in self.bins.items()},
                "events_deque": serializable_events_deque
            }
            
            with open(filepath, 'w') as f:
                json.dump(state_data, f, indent=4)
            self.last_save_time = time.time()
            logger.info(f"ClusterAggregator state saved to {filepath}")
        except Exception as e:
            logger.error(f"Error saving ClusterAggregator state: {e}")

    async def periodic_save(self):
        while True:
            await asyncio.sleep(self.config.CLUSTER_PERSISTENCE_INTERVAL_S)
            if self.config.PERSIST_CLUSTERS:
                self._save_state()
            for sym in list(self.bins.keys()):
                health = self.check_cluster_health(sym)
                if health["total_events"] == 0:
                    logger.warning(f"[{sym}] No liquidation events in window — possible WS disconnect?")

    def check_cluster_health(self, symbol: str) -> Dict:
        """Returns cluster health summary for monitoring and WS disconnect detection."""
        if symbol not in self.bins:
            return {"symbol": symbol, "total_volume": 0, "total_events": 0, "active_bins": 0}
        total_volume = sum(b["volume"] for b in self.bins[symbol].values())
        total_events = sum(b["events"] for b in self.bins[symbol].values())
        return {
            "symbol": symbol,
            "total_volume": total_volume,
            "total_events": total_events,
            "active_bins": len(self.bins[symbol]),
            "timestamp": time.time()
        }

    async def process_event(self, event: Any):
        """
        Processes a single event from the event stream.
        This method replaces the _run_queue_consumer logic for deterministic simulation.
        """
        try:
            logger.debug(f"[process_event] Event received: {type(event).__name__} for symbol: {getattr(event, 'symbol', 'N/A')}")
            self.ingest(event)
            
            # Track first event timestamp for timeout-based initial data ready
            if self._initial_data_first_received == 0:
                self._initial_data_first_received = time.time()
            
            # In simulation mode, initial data ready event is set by the sim_events_generator
            # and there's no event_queue to get from.
            # This method is primarily for consuming pre-generated events.
            if not self.config.SIM_MODE: # Only mark initial data if not in simulation mode
                event_type_str = ""
                if isinstance(event, LiquidationEvent):
                    event_type_str = "liquidation"
                elif isinstance(event, TradeEvent):
                    event_type_str = "trade"
                elif isinstance(event, OrderBookEvent):
                    event_type_str = "orderbook"
                
                logger.debug(f"[process_event] Determined event_type_str: {event_type_str}")
                logger.debug(f"Consumed {type(event).__name__} event for {event.symbol} and ingested into aggregator.")

                # Mark initial data received for this symbol and event type
                if event_type_str and event.symbol in self.initial_data_received_for_symbols and not self.initial_data_received_for_symbols[event.symbol][event_type_str]:
                    self.initial_data_received_for_symbols[event.symbol][event_type_str] = True
                    logger.info(f"Initial {event_type_str} data received for {event.symbol}. Current status: {self.initial_data_received_for_symbols[event.symbol]}")
                    self._check_initial_data_ready()
                else:
                    logger.debug(f"[{event.symbol}] {event_type_str} data already received or event_type_str is empty or symbol not in list.")

        except Exception as e:
            logger.error(f"Error in processing event: {e}", exc_info=True)

    async def _run_queue_consumer(self):
        """
        Consumes events from the event queue and ingests them into the aggregator.
        Used only in live mode.
        """
        if self.event_queue is None:
            logger.error("Attempted to run queue consumer without an event queue. This should only happen in live mode.")
            return

        logger.info(f"Starting unified event queue consumer...")
        while True:
            try:
                event = await self.event_queue.get()
                logger.debug(f"[_run_queue_consumer] Event received from queue: {type(event).__name__} for symbol: {getattr(event, 'symbol', 'N/A')}")
                self.ingest(event)
                
                event_type_str = ""
                if isinstance(event, LiquidationEvent):
                    event_type_str = "liquidation"
                elif isinstance(event, TradeEvent):
                    event_type_str = "trade"
                elif isinstance(event, OrderBookEvent):
                    event_type_str = "orderbook"
                
                logger.debug(f"[_run_queue_consumer] Determined event_type_str: {event_type_str}")
                logger.debug(f"Consumed {type(event).__name__} event for {event.symbol} and ingested into aggregator.")

                # Mark initial data received for this symbol and event type
                if event_type_str and event.symbol in self.initial_data_received_for_symbols and not self.initial_data_received_for_symbols[event.symbol][event_type_str]:
                    self.initial_data_received_for_symbols[event.symbol][event_type_str] = True
                    logger.info(f"Initial {event_type_str} data received for {event.symbol}. Current status: {self.initial_data_received_for_symbols[event.symbol]}")
                    self._check_initial_data_ready()
                else:
                    logger.debug(f"[{event.symbol}] {event_type_str} data already received or event_type_str is empty or symbol not in list.")

            except Exception as e:
                logger.error(f"Error in unified event queue consumer: {e}", exc_info=True)

    def _check_initial_data_ready(self):
        """Checks if initial data has been received for all symbols across all relevant event types."""
        if not self.initial_data_ready_event.is_set():
            logger.debug(f"Checking if initial data is ready. Current state: {self.initial_data_received_for_symbols}")
            
            # Timeout: if any data received and 30s+ passed, mark remaining as received
            if self._initial_data_first_received > 0:
                elapsed = time.time() - self._initial_data_first_received
                if elapsed > 30:
                    for symbol in self.initial_data_received_for_symbols:
                        for event_type in self.initial_data_received_for_symbols[symbol]:
                            if not self.initial_data_received_for_symbols[symbol][event_type]:
                                self.initial_data_received_for_symbols[symbol][event_type] = True
                                logger.info(f"[{symbol}] {event_type} marked as received (30s timeout reached)")
                    self.initial_data_ready_event.set()
                    logger.info("Initial data ready (30s timeout).")
                    return
            
            all_symbols_ready = True
            for symbol, types_received in self.initial_data_received_for_symbols.items():
                for event_type, received in types_received.items():
                    # Only consider event types that are actually enabled in config
                    is_enabled = False
                    if event_type == 'liquidation' and (self.config.BYBIT_LIQUIDATION_WS_ENABLED or self.config.BINANCE_LIQUIDATION_WS_ENABLED):
                        is_enabled = True
                    elif event_type == 'orderbook' and self.config.ORDERBOOK_WS_ENABLED:
                        is_enabled = True
                    elif event_type == 'trade' and self.config.TRADES_WS_ENABLED:
                        is_enabled = True
                    
                    # In SIM_MODE, all event types are considered enabled if USE_SIM_EVENTS_GENERATOR is true
                    if self.config.SIM_MODE: # Changed from USE_SIM_EVENTS_GENERATOR
                        is_enabled = True

                    if is_enabled:
                        if not received:
                            logger.debug(f"[{symbol}] Initial data NOT ready: {event_type} not yet received.")
                            all_symbols_ready = False
                            break
                        else:
                            logger.debug(f"[{symbol}] Initial data ready for {event_type}.")
                if not all_symbols_ready:
                    break
            
            if all_symbols_ready:
                self.initial_data_ready_event.set()
                logger.info("Initial data ready for all symbols across all relevant streams.")
            else:
                logger.debug(f"Not all initial data received yet. Current status: {self.initial_data_received_for_symbols}")

    async def _start_consumers(self):
        """Starts the event queue consumer if an event_queue is provided."""
        if self.event_queue is not None:
            logger.info("Starting ClusterAggregator unified consumer...")
            asyncio.create_task(self._run_queue_consumer())
        else:
            logger.info("ClusterAggregator is in SIM_MODE, no queue consumer started.")

# Example usage (for testing)
async def main():
    from .ws_trades import TradeEvent
    from .ws_orderbook import OrderBookEvent
    from .status_tracker import PairStatus # Import PairStatus

    class MockStatusTracker:
        def __init__(self):
            self.status = defaultdict(lambda: PairStatus(symbol="MOCK"))
        def update_status(self, **kwargs):
            pass

    class MockConfig:
        SYMBOLS = ["SOL/USDT", "BTC/USDT"]
        BIN_MODE = "percent"
        BIN_PCT = 0.002
        SLIDING_WINDOW_S = 300
        SWEEP_WINDOW_S = 60
        MIN_SWEEP_VOLUME_USDT = 50000.0
        SWEEP_THRESHOLD_FACTOR = 2.0
        PERSIST_CLUSTERS = False
        DATA_DIR = "./data/live" # Define DATA_DIR
        CLUSTER_STATE_FILE = "cluster_state.json" # Define CLUSTER_STATE_FILE
        SIM_MODE = True # Set to True for testing purposes
        BYBIT_LIQUIDATION_WS_ENABLED = True
        BINANCE_LIQUIDATION_WS_ENABLED = True
        ORDERBOOK_WS_ENABLED = True
        TRADES_WS_ENABLED = True


    mock_config = MockConfig()
    mock_status_tracker = MockStatusTracker()
    
    # In SIM_MODE, ClusterAggregator is initialized without event_queue
    aggregator = ClusterAggregator(mock_config, mock_status_tracker) 
    
    # Simulate some events by putting them into the unified queue
    events_to_simulate = [
        LiquidationEvent("bybit", "SOL/USDT", int(time.time()*1000) - 1000, 20.0, 10.0, 200.0, "LONG"),
        LiquidationEvent("bybit", "SOL/USDT", int(time.time()*1000) - 500, 20.1, 5.0, 100.0, "SHORT"),
        LiquidationEvent("bybit", "SOL/USDT", int(time.time()*1000), 20.0, 15.0, 300.0, "LONG"),
        LiquidationEvent("bybit", "BTC/USDT", int(time.time()*1000), 30000.0, 0.01, 300.0, "SHORT"),
        TradeEvent("bybit", "SOL/USDT", int(time.time()*1000), 20.0, 10.0, "buy", "trade1", 0.6),
        OrderBookEvent("bybit", "SOL/USDT", int(time.time()*1000), 20.05, 0.55),
    ]

    for event in events_to_simulate:
        await aggregator.process_event(event) # Process events directly
    
    print("\n--- SOL/USDT Clusters ---")
    # Ensure initial data is marked ready for simulation to proceed
    # In deterministic simulation, initial data is ready immediately after processing events
    for symbol in mock_config.SYMBOLS:
        for event_type in ['liquidation', 'orderbook', 'trade']:
            aggregator.initial_data_received_for_symbols[symbol][event_type] = True
    aggregator._check_initial_data_ready()


    sol_snapshot = aggregator.get_snapshot("SOL/USDT")
    print(json.dumps(sol_snapshot, indent=2))
    print(f"Bin strength at 20.0 for SOL/USDT: {aggregator.get_bin_strength_at('SOL/USDT', 20.0)}")
    print(f"Sweep detected at 20.0 for SOL/USDT: {aggregator.is_sweep_detected('SOL/USDT', 20.0)}")
    print(f"SOL/USDT Trade Imbalance: {aggregator.get_trade_imbalance('SOL/USDT')}")
    print(f"SOL/USDT Orderbook Mid Price: {aggregator.get_orderbook_mid_price('SOL/USDT')}")
    print(f"SOL/USDT Orderbook Imbalance: {aggregator.get_orderbook_imbalance('SOL/USDT')}")


    print("\n--- BTC/USDT Clusters ---")
    btc_snapshot = aggregator.get_snapshot("BTC/USDT")
    print(json.dumps(btc_snapshot, indent=2))

    # Test persistence
    if mock_config.PERSIST_CLUSTERS:
        print("\n--- Saving state ---")
        aggregator._save_state()
        
        print("\n--- Loading state into new aggregator ---")
        new_aggregator = ClusterAggregator(mock_config, mock_status_tracker) # Removed asyncio.Queue()
        sol_snapshot_loaded = new_aggregator.get_snapshot("SOL/USDT")
        print(json.dumps(sol_snapshot_loaded, indent=2))
        
        # Verify loaded data
        assert sol_snapshot["median_volume"] == sol_snapshot_loaded["median_volume"]
        print("State loaded and verified successfully!")


if __name__ == "__main__":
    asyncio.run(main())
